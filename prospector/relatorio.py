"""Planilha de leads (CSV) — registro de tudo o que foi triado, inclusive os descartados."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from .models import Lead
from .places import MOTIVOS_LEGIVEIS

COLUNAS = [
    "categoria_pesquisada", "nome", "situacao", "motivo", "nota", "avaliacoes", "telefone",
    "endereco", "redes_sociais", "google_maps", "proposta_pdf", "texto_da_proposta", "erro",
]


def _escrever(leads: list[Lead], arquivo) -> None:
    escritor = csv.writer(arquivo, delimiter=";")
    escritor.writerow(COLUNAS)
    for lead in leads:
        n = lead.business
        escritor.writerow(
            [
                lead.search_category,
                n.name,
                "aprovado" if lead.approved else "descartado",
                MOTIVOS_LEGIVEIS.get(lead.reason, lead.reason),
                "" if n.rating is None else str(n.rating).replace(".", ","),
                "" if n.reviews is None else n.reviews,
                n.phone or "",
                n.address,
                ", ".join(n.social_links),
                n.maps_url or "",
                lead.proposal_pdf or "",
                {"llm": "gerado por IA", "padrao": "texto padrão"}.get(lead.proposal_source or "", ""),
                lead.error or "",
            ]
        )


def salvar_csv(leads: list[Lead], destino: Path) -> Path:
    """Grava leads.csv com ';' e BOM, o formato que o Excel em português abre sem importação."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8-sig") as arquivo:
        _escrever(leads, arquivo)
    return destino


def csv_bytes(leads: list[Lead]) -> bytes:
    """O mesmo CSV em memória (com BOM), para download no app web."""
    buffer = io.StringIO(newline="")
    _escrever(leads, buffer)
    return buffer.getvalue().encode("utf-8-sig")
