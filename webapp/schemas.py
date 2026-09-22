"""Formatos de entrada e saída da API.

O servidor NÃO guarda estado entre chamadas (a Vercel roda funções sem memória
compartilhada). Por isso o navegador devolve, a cada chamada, os dados do lead e da proposta,
e o servidor valida tudo de novo aqui: tamanhos e tipos limitados, mesmo que a página seja
alterada por quem a estiver usando.
"""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, Field, field_validator

from prospector.models import Business, Lead, Proposal
from prospector.places import MOTIVOS_LEGIVEIS


# "latitude,longitude,zoom", ex.: "-22.9527,-46.5419,13z" (o mesmo formato de LOCATION_COORDINATE no .env).
_LOCALIZACAO = re.compile(r"-?\d{1,3}(\.\d+)?,-?\d{1,3}(\.\d+)?,\d{1,2}z")


def chave_lead(place_id: str) -> str:
    """Identificador curto e seguro para usar no navegador (o place_id pode ter caracteres estranhos)."""
    return hashlib.sha1(place_id.encode("utf-8")).hexdigest()[:12]


def _itens_curtos(valores: list[str], maximo: int) -> list[str]:
    for v in valores:
        if not isinstance(v, str) or len(v) > maximo:
            raise ValueError(f"cada item deve ser um texto de até {maximo} caracteres")
    return valores


# ---------- Entrada ----------


class PedidoBusca(BaseModel):
    category: str = Field(min_length=1)
    min_rating: float = Field(ge=0, le=5)
    min_reviews: int = Field(ge=0, le=1_000_000)
    depth: int = Field(ge=1, le=700)
    allow_no_phone: bool = False
    demo: bool = False
    # Deixe em branco (None) para usar a cidade/localização padrão do servidor. Ignorados no modo
    # demonstração: os dados fictícios são sempre de Bragança Paulista.
    city_name: str | None = Field(default=None, max_length=120)
    location_coordinate: str | None = Field(default=None, max_length=40)

    @field_validator("category")
    @classmethod
    def _categoria(cls, valor: str) -> str:
        limpa = " ".join(valor.split())
        if not limpa or len(limpa) > 60:
            raise ValueError("A categoria deve ter de 1 a 60 caracteres.")
        return limpa

    @field_validator("city_name")
    @classmethod
    def _cidade(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        limpa = " ".join(valor.split())
        return limpa or None

    @field_validator("location_coordinate")
    @classmethod
    def _localizacao(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        limpa = valor.strip()
        if not limpa:
            return None
        if not _LOCALIZACAO.fullmatch(limpa):
            raise ValueError(
                "A localização deve estar no formato 'latitude,longitude,zoom' (ex.: -22.9527,-46.5419,13z)."
            )
        return limpa


class Opcoes(BaseModel):
    """Condições comerciais que aparecem no PDF."""

    price_total: float = Field(gt=0, le=10_000_000)
    installments: int = Field(ge=1, le=24)
    delivery_days: int = Field(ge=1, le=365)
    validity_days: int = Field(ge=1, le=365)


class LeadEntrada(BaseModel):
    place_id: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="", max_length=120)
    address: str = Field(default="", max_length=400)
    phone: str | None = Field(default=None, max_length=40)
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews: int | None = Field(default=None, ge=0, le=100_000_000)
    social_links: list[str] = Field(default_factory=list, max_length=10)
    is_claimed: bool | None = None
    has_hours: bool = False
    maps_url: str | None = Field(default=None, max_length=300)
    approved: bool = True
    reason: str = Field(default="ok", max_length=40)
    search_category: str = Field(default="", max_length=60)
    proposal_source: str | None = Field(default=None, max_length=10)
    error: str | None = Field(default=None, max_length=500)

    @field_validator("social_links")
    @classmethod
    def _redes(cls, valores: list[str]) -> list[str]:
        return _itens_curtos(valores, 30)

    def para_lead(self) -> Lead:
        negocio = Business(
            place_id=self.place_id,
            name=self.name,
            category=self.category,
            address=self.address,
            phone=self.phone or None,
            rating=self.rating,
            reviews=self.reviews,
            website_domain=None,
            website_url=None,
            social_links=list(self.social_links),
            is_claimed=self.is_claimed,
            has_hours=self.has_hours,
            maps_url=self.maps_url,
        )
        return Lead(
            business=negocio,
            approved=self.approved,
            reason=self.reason,
            search_category=self.search_category,
            proposal_source=self.proposal_source,
            error=self.error,
        )


class ItemEscopo(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=400)


class PropostaEntrada(BaseModel):
    summary: str = Field(min_length=1, max_length=600)
    strengths: list[str] = Field(min_length=1, max_length=6)
    weaknesses: list[str] = Field(min_length=1, max_length=6)
    scope: list[ItemEscopo] = Field(min_length=1, max_length=8)
    arguments: list[str] = Field(min_length=1, max_length=6)
    closing: str = Field(min_length=1, max_length=400)
    source: str = Field(pattern="^(llm|padrao)$")

    @field_validator("strengths", "weaknesses", "arguments")
    @classmethod
    def _itens(cls, valores: list[str]) -> list[str]:
        return _itens_curtos(valores, 400)

    def para_proposta(self) -> Proposal:
        return Proposal(
            summary=self.summary,
            strengths=list(self.strengths),
            weaknesses=list(self.weaknesses),
            scope=[(i.name, i.description) for i in self.scope],
            arguments=list(self.arguments),
            closing=self.closing,
            source=self.source,
        )


class PedidoProposta(BaseModel):
    lead: LeadEntrada
    use_llm: bool = True


class PedidoPdf(Opcoes):
    lead: LeadEntrada
    proposal: PropostaEntrada


class ItemZip(BaseModel):
    lead: LeadEntrada
    proposal: PropostaEntrada


class PedidoZip(Opcoes):
    items: list[ItemZip] = Field(min_length=1)


class PedidoCsv(BaseModel):
    leads: list[LeadEntrada] = Field(min_length=1, max_length=2000)


# ---------- Saída ----------


def lead_para_json(lead: Lead) -> dict:
    n = lead.business
    return {
        "id": chave_lead(n.place_id),
        "place_id": n.place_id,
        "name": n.name,
        "category": n.category,
        "address": n.address,
        "phone": n.phone,
        "rating": n.rating,
        "reviews": n.reviews,
        "social_links": n.social_links,
        "is_claimed": n.is_claimed,
        "has_hours": n.has_hours,
        "maps_url": n.maps_url,
        "approved": lead.approved,
        "reason": lead.reason,
        "reason_text": MOTIVOS_LEGIVEIS.get(lead.reason, lead.reason),
        "search_category": lead.search_category,
    }


def proposta_para_json(p: Proposal) -> dict:
    return {
        "summary": p.summary,
        "strengths": p.strengths,
        "weaknesses": p.weaknesses,
        "scope": [{"name": nome, "description": descricao} for nome, descricao in p.scope],
        "arguments": p.arguments,
        "closing": p.closing,
        "source": p.source,
    }
