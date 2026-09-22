"""API e página do app web.

SEM ESTADO NO SERVIDOR. A Vercel executa cada requisição numa função separada, sem memória
compartilhada e sem processos em segundo plano. Por isso o navegador conduz o fluxo
(uma chamada por categoria, uma por proposta) e o servidor faz uma única coisa por chamada.

Rotas (exigem login, exceto GET /api/config e os arquivos da página):
  GET  /api/config     dados públicos para montar a tela e fazer login
  POST /api/search     busca UMA categoria e devolve os leads já triados
  POST /api/proposal   escreve a proposta de UM lead (chama o LLM, se pedido)
  POST /api/pdf        monta o PDF de um lead (sem custo: não chama a AIsa)
  POST /api/zip        monta um ZIP com vários PDFs
  POST /api/csv        monta a planilha de leads

Erros da AIsa nunca voltam como 401/403 (a página trataria como "sessão expirada"):
  424 = problema de conta/chave/rota na AIsa (repetir não adianta: a página para o lote)
  502 = falha momentânea da AIsa (rede, instabilidade)
"""

from __future__ import annotations

import dataclasses
import io
import logging
import threading
import zipfile
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from prospector.aisa_client import AisaClient, AisaError
from prospector.config import Config
from prospector.demo_data import ITENS_DEMO
from prospector.pdf_export import gerar_pdf_bytes
from prospector.places import buscar_leads, slug
from prospector.proposal import gerar_proposta
from prospector.relatorio import csv_bytes

from .auth import criar_verificador_firebase, obter_usuario
from .schemas import (
    PedidoBusca,
    PedidoCsv,
    PedidoPdf,
    PedidoProposta,
    PedidoZip,
    lead_para_json,
    proposta_para_json,
)
from .settings import WebSettings

log = logging.getLogger(__name__)

PASTA_ESTATICA = Path(__file__).resolve().parent / "static"
ERROS_DE_CONTA = (401, 402, 403, 404)


def erro_da_aisa(erro: AisaError) -> HTTPException:
    codigo = 424 if erro.status in ERROS_DE_CONTA else 502
    return HTTPException(status_code=codigo, detail=str(erro))


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
    app.state.verificar_token = verificar_token or (
        criar_verificador_firebase(settings.firebase_project_id) if settings.auth_mode == "firebase" else None
    )

    _cliente: dict = {}
    _trava = threading.Lock()

    def cliente_aisa() -> AisaClient:
        if not cfg.api_key:
            raise HTTPException(400, "AISA_API_KEY não configurada no servidor. Use o modo demonstração ou configure a chave.")
        with _trava:
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

    # ----- rotas -----

    @app.get("/api/config")
    def configuracao():
        return {
            "authMode": settings.auth_mode,
            "firebase": settings.firebase_public(),
            "hasApiKey": bool(cfg.api_key),
            "city": cfg.city_name,
            "locationCoordinate": cfg.location_coordinate,
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

    @app.post("/api/search")
    def buscar(pedido: PedidoBusca, usuario: str = Depends(obter_usuario)):
        if pedido.depth > settings.max_depth:
            raise HTTPException(422, f"A profundidade máxima é {settings.max_depth}.")
        cliente = None if pedido.demo else cliente_aisa()
        # No modo demonstração a cidade/localização ficam sempre no padrão: os dados fictícios são
        # fixos em Bragança Paulista, então trocar a cidade só descartaria tudo por "fora_da_cidade".
        overrides = {}
        if not pedido.demo:
            if pedido.city_name:
                overrides["city_name"] = pedido.city_name
            if pedido.location_coordinate:
                overrides["location_coordinate"] = pedido.location_coordinate
        cfg_busca = dataclasses.replace(cfg, min_rating=pedido.min_rating, min_reviews=pedido.min_reviews, **overrides)
        try:
            leads = buscar_leads(
                cliente,
                cfg_busca,
                list(ITENS_DEMO) if pedido.demo else [pedido.category],
                pedido.depth,
                itens_demo=ITENS_DEMO if pedido.demo else None,
                permitir_sem_telefone=pedido.allow_no_phone,
            )
        except AisaError as erro:
            raise erro_da_aisa(erro) from None
        return {"leads": [lead_para_json(l) for l in leads]}

    @app.post("/api/proposal")
    def proposta(pedido: PedidoProposta, usuario: str = Depends(obter_usuario)):
        lead = pedido.lead.para_lead()
        if not lead.approved:
            raise HTTPException(422, "Só é possível gerar proposta para leads aprovados.")
        cliente = cliente_aisa() if pedido.use_llm else None
        try:
            texto = gerar_proposta(cliente, lead.business, cfg, usar_llm=pedido.use_llm)
        except AisaError as erro:
            raise erro_da_aisa(erro) from None
        return {"proposal": proposta_para_json(texto)}

    def cfg_da_proposta(opcoes) -> Config:
        return dataclasses.replace(
            cfg,
            price_total=opcoes.price_total,
            installments=opcoes.installments,
            delivery_days=opcoes.delivery_days,
            validity_days=opcoes.validity_days,
        )

    @app.post("/api/pdf")
    def baixar_pdf(pedido: PedidoPdf, usuario: str = Depends(obter_usuario)):
        negocio = pedido.lead.para_lead().business
        conteudo = gerar_pdf_bytes(negocio, pedido.proposal.para_proposta(), cfg_da_proposta(pedido))
        return Response(
            conteudo,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="proposta-{slug(negocio.name)}.pdf"'},
        )

    @app.post("/api/zip")
    def baixar_zip(pedido: PedidoZip, usuario: str = Depends(obter_usuario)):
        if len(pedido.items) > settings.max_proposals:
            raise HTTPException(422, f"No máximo {settings.max_proposals} propostas por arquivo.")
        cfg_pdf = cfg_da_proposta(pedido)
        buffer = io.BytesIO()
        usados: set[str] = set()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as arquivo:
            for item in pedido.items:
                negocio = item.lead.para_lead().business
                base = f"proposta-{slug(negocio.name)}"
                nome, contador = base, 1
                while nome in usados:  # dois negócios com o mesmo nome (ou o mesmo lead repetido)
                    contador += 1
                    nome = f"{base}-{contador}"
                usados.add(nome)
                arquivo.writestr(f"{nome}.pdf", gerar_pdf_bytes(negocio, item.proposal.para_proposta(), cfg_pdf))
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="propostas.zip"'},
        )

    @app.post("/api/csv")
    def baixar_csv(pedido: PedidoCsv, usuario: str = Depends(obter_usuario)):
        return Response(
            csv_bytes([l.para_lead() for l in pedido.leads]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="leads.csv"'},
        )

    # ----- página -----

    @app.get("/", include_in_schema=False)
    def pagina():
        return FileResponse(PASTA_ESTATICA / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=PASTA_ESTATICA), name="static")
    return app
