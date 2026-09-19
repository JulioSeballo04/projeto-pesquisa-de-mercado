"""Estruturas de dados compartilhadas entre os módulos.

Ficam separadas para que busca, proposta e PDF conversem por "contratos" simples
e possam ser testados um sem o outro.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Business:
    """Um estabelecimento como veio do Google Maps, já normalizado."""

    place_id: str
    name: str
    category: str
    address: str
    phone: str | None
    rating: float | None
    reviews: int | None
    website_domain: str | None  # domínio do site, se houver algum link cadastrado
    website_url: str | None
    social_links: list[str] = field(default_factory=list)  # ex.: ["Instagram", "WhatsApp"]
    is_claimed: bool | None = None  # dono já reivindicou o perfil no Google?
    has_hours: bool = False  # horário de funcionamento cadastrado?
    maps_url: str | None = None


@dataclass
class Lead:
    """Resultado da triagem de um estabelecimento."""

    business: Business
    approved: bool
    reason: str  # "ok" quando aprovado; senão o motivo do descarte
    search_category: str = ""  # termo pesquisado que trouxe este resultado
    proposal_pdf: str | None = None
    proposal_source: str | None = None  # "llm" ou "padrao"
    error: str | None = None


@dataclass
class Proposal:
    """Texto da proposta (a parte que varia de empresa para empresa)."""

    summary: str
    strengths: list[str]
    weaknesses: list[str]
    scope: list[tuple[str, str]]  # (nome do item, descrição)
    arguments: list[str]
    closing: str
    source: str  # "llm" ou "padrao"
