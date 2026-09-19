"""Tarefas em segundo plano (busca de leads e geração de propostas).

Ficam em memória, num único processo. Para 2 ou 3 pessoas isso basta e evita ter banco de
dados. A consequência: se o servidor reiniciar, as tarefas em andamento somem (é só buscar
de novo). Por isso o app deve rodar com um único "worker".
"""

from __future__ import annotations

import dataclasses
import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field

from prospector.aisa_client import AisaClient, AisaError
from prospector.config import Config
from prospector.demo_data import ITENS_DEMO
from prospector.models import Lead, Proposal
from prospector.places import MOTIVOS_LEGIVEIS, buscar_leads
from prospector.proposal import gerar_proposta

ERROS_FATAIS = (401, 402, 403, 404)


@dataclass
class RegistroLead:
    lead: Lead
    proposal_status: str = "nenhuma"  # nenhuma | gerando | pronta | erro
    proposal: Proposal | None = None
    proposal_cfg: Config | None = None  # config usada (preço/prazo) — necessária para montar o PDF depois
    error: str | None = None


@dataclass
class Job:
    id: str
    owner: str
    demo: bool
    status: str = "running"  # running | done | error
    phase: str = "busca"  # busca | propostas
    error: str | None = None
    progress: list[str] = field(default_factory=list)
    leads: dict[str, RegistroLead] = field(default_factory=dict)
    criado_em: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def log(self, mensagem: str) -> None:
        with self.lock:
            self.progress.append(mensagem)
            del self.progress[:-50]  # guarda só as últimas mensagens

    def snapshot(self) -> dict:
        """Estado serializável para a página (sem dados internos como o Config)."""
        with self.lock:
            return {
                "id": self.id,
                "status": self.status,
                "phase": self.phase,
                "demo": self.demo,
                "error": self.error,
                "progress": list(self.progress),
                "leads": [lead_para_json(r) for r in self.leads.values()],
            }


def chave_lead(place_id: str) -> str:
    """Identificador curto e seguro para URL (o place_id do Google pode ter caracteres estranhos)."""
    return hashlib.sha1(place_id.encode("utf-8")).hexdigest()[:12]


def lead_para_json(reg: RegistroLead) -> dict:
    n = reg.lead.business
    return {
        "id": chave_lead(n.place_id),
        "name": n.name,
        "category": n.category,
        "searchCategory": reg.lead.search_category,
        "address": n.address,
        "phone": n.phone,
        "rating": n.rating,
        "reviews": n.reviews,
        "socialLinks": n.social_links,
        "mapsUrl": n.maps_url,
        "approved": reg.lead.approved,
        "reason": reg.lead.reason,
        "reasonText": MOTIVOS_LEGIVEIS.get(reg.lead.reason, reg.lead.reason),
        "proposalStatus": reg.proposal_status,
        "proposalSource": reg.proposal.source if reg.proposal else None,
        "error": reg.error,
    }


class JobStore:
    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def _limpar(self) -> None:
        limite = time.time() - self.ttl
        for job_id in [i for i, j in self._jobs.items() if j.criado_em < limite and j.status != "running"]:
            del self._jobs[job_id]

    def _ocupado(self, owner: str) -> bool:
        return any(j.owner == owner and j.status == "running" for j in self._jobs.values())

    def criar_exclusivo(self, owner: str, demo: bool) -> Job | None:
        """Cria uma tarefa, a menos que a pessoa já tenha uma rodando (evita clique duplo = custo dobrado).

        A checagem e a criação acontecem sob o mesmo trava, para que dois pedidos simultâneos
        não passem os dois.
        """
        with self._lock:
            self._limpar()
            if self._ocupado(owner):
                return None
            job = Job(id=uuid.uuid4().hex, owner=owner, demo=demo)
            self._jobs[job.id] = job
            return job

    def retomar_exclusivo(self, job: Job) -> bool:
        """Marca a tarefa como "gerando propostas", se a pessoa não tiver outra rodando."""
        with self._lock:
            if self._ocupado(job.owner):
                return False
            with job.lock:
                job.status, job.phase, job.error = "running", "propostas", None
            return True

    def obter(self, job_id: str, owner: str) -> Job | None:
        # Cada pessoa só enxerga as próprias tarefas.
        with self._lock:
            job = self._jobs.get(job_id)
        return job if job and job.owner == owner else None



# ---------- Trabalhadores (rodam em threads) ----------


def executar_busca(
    job: Job,
    cfg: Config,
    cliente: AisaClient | None,
    categorias: list[str],
    profundidade: int,
    permitir_sem_telefone: bool,
) -> None:
    try:
        if job.demo:
            categorias = list(ITENS_DEMO)  # no modo demonstração as categorias são as dos dados fictícios
        leads = buscar_leads(
            cliente, cfg, categorias, profundidade,
            itens_demo=ITENS_DEMO if job.demo else None,
            permitir_sem_telefone=permitir_sem_telefone,
            progresso=job.log,
        )
        with job.lock:
            job.leads = {chave_lead(l.business.place_id): RegistroLead(lead=l) for l in leads}
        aprovados = sum(1 for l in leads if l.approved)
        job.log(f"Pronto: {len(leads)} estabelecimento(s) triado(s), {aprovados} aprovado(s).")
        with job.lock:
            job.status = "done"
    except AisaError as erro:
        with job.lock:
            job.status, job.error = "error", str(erro)
    except Exception as erro:  # nunca deixar a tarefa "presa" em running
        with job.lock:
            job.status, job.error = "error", f"Erro inesperado: {type(erro).__name__}."


def executar_propostas(
    job: Job,
    cfg: Config,
    cliente: AisaClient | None,
    place_ids: list[str],
    usar_llm: bool,
) -> None:
    fatal: str | None = None
    try:
        for place_id in place_ids:
            reg = job.leads[place_id]
            if fatal:  # erro de conta/rota: as próximas chamadas falhariam do mesmo jeito
                with job.lock:
                    reg.proposal_status, reg.error = "erro", "Não gerada: " + fatal
                continue
            with job.lock:
                reg.proposal_status, reg.error = "gerando", None
            job.log(f"Gerando proposta de '{reg.lead.business.name}'...")
            try:
                proposta = gerar_proposta(cliente, reg.lead.business, cfg, usar_llm=usar_llm)
                with job.lock:
                    reg.proposal, reg.proposal_cfg = proposta, cfg
                    reg.lead.proposal_source = proposta.source
                    reg.proposal_status = "pronta"
            except AisaError as erro:
                with job.lock:
                    reg.proposal_status, reg.error = "erro", str(erro)
                if erro.status in ERROS_FATAIS:
                    fatal = str(erro)
            except Exception as erro:
                with job.lock:
                    reg.proposal_status, reg.error = "erro", f"Erro inesperado: {type(erro).__name__}."
        job.log("Propostas concluídas.")
    finally:
        with job.lock:
            job.status = "done"
            job.phase = "propostas"
            if fatal:
                job.error = fatal


def cfg_com(cfg: Config, **mudancas) -> Config:
    return dataclasses.replace(cfg, **mudancas)
