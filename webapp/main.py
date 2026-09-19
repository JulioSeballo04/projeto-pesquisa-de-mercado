"""API e páginas do app web.

Rotas (todas exigem login, exceto /api/config e os arquivos da página):
  GET  /api/config                        dados públicos para montar a tela e fazer login
  POST /api/search                        inicia a busca de leads         -> {jobId}
  GET  /api/jobs/{id}                     estado da tarefa (a página consulta a cada ~1,5 s)
  POST /api/jobs/{id}/proposals           gera propostas dos leads escolhidos
  GET  /api/jobs/{id}/leads/{lead}/pdf    PDF de uma proposta pronta
  GET  /api/jobs/{id}/proposals.zip       todas as propostas prontas
  GET  /api/jobs/{id}/leads.csv           planilha de todos os leads
"""

from __future__ import annotations

import io
import logging
import threading
import zipfile
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from prospector.aisa_client import AisaClient
from prospector.config import Config, ConfigError
from prospector.pdf_export import gerar_pdf_bytes
from prospector.places import slug
from prospector.relatorio import csv_bytes

from .auth import criar_verificador_firebase, obter_usuario
from .jobs import JobStore, cfg_com, executar_busca, executar_propostas
from .settings import WebSettings

log = logging.getLogger(__name__)

PASTA_ESTATICA = Path(__file__).resolve().parent / "static"


# ---------- Modelos de entrada (a validação acontece aqui, no servidor) ----------


class PedidoBusca(BaseModel):
    categories: list[str] = Field(min_length=1)
    min_rating: float = Field(ge=0, le=5)
    min_reviews: int = Field(ge=0, le=1_000_000)
    depth: int = Field(ge=1, le=700)
    allow_no_phone: bool = False
    demo: bool = False

    @field_validator("categories")
    @classmethod
    def _categorias(cls, valor: list[str]) -> list[str]:
        limpas: list[str] = []
        for c in valor:
            c = " ".join(c.split())
            if not c or len(c) > 60:
                raise ValueError("Cada categoria deve ter de 1 a 60 caracteres.")
            if c.lower() not in (x.lower() for x in limpas):
                limpas.append(c)
        return limpas


class PedidoPropostas(BaseModel):
    lead_ids: list[str] = Field(min_length=1)
    use_llm: bool = True
    price_total: float = Field(gt=0, le=10_000_000)
    installments: int = Field(ge=1, le=24)
    delivery_days: int = Field(ge=1, le=365)
    validity_days: int = Field(ge=1, le=365)


# ---------- Fábrica do app ----------


def criar_app(
    settings: WebSettings | None = None,
    cfg: Config | None = None,
    verificar_token: Callable[[str], str] | None = None,
    fabrica_cliente: Callable[[Config], AisaClient] | None = None,
) -> FastAPI:
    """Monta o app. Os parâmetros existem para os testes injetarem dados falsos."""
    settings = settings or WebSettings.from_env()
    cfg = cfg or Config.from_env(require_api_key=False)
    fabrica_cliente = fabrica_cliente or (lambda c: AisaClient(c.api_key, c.base_url))

    app = FastAPI(title="Prospector", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.cfg = cfg
    app.state.jobs = JobStore(settings.job_ttl_seconds)
    app.state.verificar_token = verificar_token or (
        criar_verificador_firebase(settings.firebase_project_id) if settings.auth_mode == "firebase" else None
    )

    _cliente: dict = {}
    _lock_cliente = threading.Lock()

    def cliente_aisa() -> AisaClient:
        if not cfg.api_key:
            raise HTTPException(400, "AISA_API_KEY não configurada no servidor. Use o modo demonstração ou configure o .env.")
        with _lock_cliente:
            if "c" not in _cliente:
                _cliente["c"] = fabrica_cliente(cfg)
            return _cliente["c"]

    @app.middleware("http")
    async def cabecalhos_de_seguranca(request: Request, call_next):
        resposta = await call_next(request)
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["Referrer-Policy"] = "no-referrer"
        resposta.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            resposta.headers["Cache-Control"] = "no-store"  # dados de empresas e propostas não ficam em cache
        return resposta

    def job_do_usuario(job_id: str, usuario: str):
        job = app.state.jobs.obter(job_id, usuario)
        if job is None:
            raise HTTPException(404, "Tarefa não encontrada (ela pode ter expirado). Faça uma nova busca.")
        return job

    # ----- rotas -----

    @app.get("/api/config")
    def configuracao():
        return {
            "authMode": settings.auth_mode,
            "firebase": settings.firebase_public(),
            "hasApiKey": bool(cfg.api_key),
            "city": cfg.city_name,
            "company": {"name": cfg.company_name},
            "defaults": {
                "categories": list(cfg.categories),
                "minRating": cfg.min_rating,
                "minReviews": cfg.min_reviews,
                "depth": min(cfg.search_depth, settings.max_depth),
                "priceTotal": cfg.price_total,
                "installments": cfg.installments,
                "deliveryDays": cfg.delivery_days,
                "validityDays": cfg.validity_days,
            },
            "limits": {
                "maxCategories": settings.max_categories,
                "maxDepth": settings.max_depth,
                "maxProposals": settings.max_proposals,
            },
        }

    @app.post("/api/search", status_code=202)
    def buscar(pedido: PedidoBusca, usuario: str = Depends(obter_usuario)):
        if len(pedido.categories) > settings.max_categories:
            raise HTTPException(422, f"No máximo {settings.max_categories} categorias por busca.")
        if pedido.depth > settings.max_depth:
            raise HTTPException(422, f"A profundidade máxima é {settings.max_depth}.")
        cliente = None if pedido.demo else cliente_aisa()

        cfg_busca = cfg_com(cfg, min_rating=pedido.min_rating, min_reviews=pedido.min_reviews)
        job = app.state.jobs.criar_exclusivo(usuario, demo=pedido.demo)
        if job is None:
            raise HTTPException(409, "Já existe uma tarefa em andamento. Aguarde terminar.")
        job.log("Busca iniciada.")
        threading.Thread(
            target=executar_busca,
            args=(job, cfg_busca, cliente, pedido.categories, pedido.depth, pedido.allow_no_phone),
            daemon=True,
        ).start()
        return {"jobId": job.id}

    @app.get("/api/jobs/{job_id}")
    def estado(job_id: str, request: Request, usuario: str = Depends(obter_usuario)):
        return job_do_usuario(job_id, usuario).snapshot()

    @app.post("/api/jobs/{job_id}/proposals", status_code=202)
    def gerar_propostas(job_id: str, pedido: PedidoPropostas, request: Request, usuario: str = Depends(obter_usuario)):
        job = job_do_usuario(job_id, usuario)
        if job.status == "running":
            raise HTTPException(409, "Esta tarefa ainda está em andamento.")
        ids = list(dict.fromkeys(pedido.lead_ids))  # sem repetidos, mantendo a ordem
        if len(ids) > settings.max_proposals:
            raise HTTPException(422, f"No máximo {settings.max_proposals} propostas por vez.")
        with job.lock:
            invalidos = [i for i in ids if i not in job.leads or not job.leads[i].lead.approved]
        if invalidos:
            raise HTTPException(422, "Só é possível gerar proposta para leads aprovados desta busca.")
        cliente = cliente_aisa() if (pedido.use_llm and not job.demo) else None
        # No modo demonstração nunca chamamos o LLM (é de graça e sem chave).
        usar_llm = pedido.use_llm and not job.demo

        cfg_proposta = cfg_com(
            cfg,
            price_total=pedido.price_total,
            installments=pedido.installments,
            delivery_days=pedido.delivery_days,
            validity_days=pedido.validity_days,
        )
        if not app.state.jobs.retomar_exclusivo(job):
            raise HTTPException(409, "Já existe uma tarefa em andamento. Aguarde terminar.")
        threading.Thread(
            target=executar_propostas, args=(job, cfg_proposta, cliente, ids, usar_llm), daemon=True
        ).start()
        return {"jobId": job.id}

    @app.get("/api/jobs/{job_id}/leads/{lead_id}/pdf")
    def baixar_pdf(job_id: str, lead_id: str, request: Request, usuario: str = Depends(obter_usuario)):
        job = job_do_usuario(job_id, usuario)
        with job.lock:
            reg = job.leads.get(lead_id)
            pronto = reg is not None and reg.proposal is not None
            if pronto:
                negocio, proposta, cfg_pdf = reg.lead.business, reg.proposal, reg.proposal_cfg
        if not pronto:
            raise HTTPException(404, "Proposta ainda não gerada para este lead.")
        conteudo = gerar_pdf_bytes(negocio, proposta, cfg_pdf)
        return Response(
            conteudo,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="proposta-{slug(negocio.name)}.pdf"'},
        )

    @app.get("/api/jobs/{job_id}/proposals.zip")
    def baixar_zip(job_id: str, request: Request, usuario: str = Depends(obter_usuario)):
        job = job_do_usuario(job_id, usuario)
        with job.lock:
            prontos = [
                (r.lead.business, r.proposal, r.proposal_cfg) for r in job.leads.values() if r.proposal is not None
            ]
        if not prontos:
            raise HTTPException(404, "Nenhuma proposta pronta para baixar.")
        buffer = io.BytesIO()
        usados: set[str] = set()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as arquivo:
            for negocio, proposta, cfg_pdf in prontos:
                nome = f"proposta-{slug(negocio.name)}"
                if nome in usados:  # dois negócios com o mesmo nome
                    nome += f"-{slug(negocio.place_id)[-6:]}"
                usados.add(nome)
                arquivo.writestr(f"{nome}.pdf", gerar_pdf_bytes(negocio, proposta, cfg_pdf))
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="propostas.zip"'},
        )

    @app.get("/api/jobs/{job_id}/leads.csv")
    def baixar_csv(job_id: str, request: Request, usuario: str = Depends(obter_usuario)):
        job = job_do_usuario(job_id, usuario)
        with job.lock:
            leads = [r.lead for r in job.leads.values()]
        return Response(
            csv_bytes(leads),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="leads.csv"'},
        )

    @app.exception_handler(ConfigError)
    async def erro_de_config(_: Request, erro: ConfigError):
        return JSONResponse({"detail": str(erro)}, status_code=400)

    # ----- página -----

    @app.get("/", include_in_schema=False)
    def pagina():
        return FileResponse(PASTA_ESTATICA / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=PASTA_ESTATICA), name="static")
    return app
