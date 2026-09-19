"""Leitura e validação da configuração (arquivo .env + variáveis de ambiente)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

RAIZ_PROJETO = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """Configuração ausente ou inválida (a mensagem já diz o que corrigir)."""


def _texto(nome: str, padrao: str) -> str:
    valor = os.getenv(nome)
    return valor.strip() if valor and valor.strip() else padrao


def _inteiro(nome: str, padrao: int, minimo: int, maximo: int) -> int:
    bruto = _texto(nome, str(padrao))
    try:
        valor = int(bruto)
    except ValueError:
        raise ConfigError(f"{nome} deve ser um número inteiro (valor atual: {bruto!r}).") from None
    if not minimo <= valor <= maximo:
        raise ConfigError(f"{nome} deve estar entre {minimo} e {maximo} (valor atual: {valor}).")
    return valor


def _decimal(nome: str, padrao: float, minimo: float, maximo: float) -> float:
    # Aceita vírgula ou ponto ("4,5" ou "4.5").
    bruto = _texto(nome, str(padrao)).replace(",", ".")
    try:
        valor = float(bruto)
    except ValueError:
        raise ConfigError(f"{nome} deve ser um número (valor atual: {bruto!r}).") from None
    if not minimo <= valor <= maximo:
        raise ConfigError(f"{nome} deve estar entre {minimo} e {maximo} (valor atual: {valor}).")
    return valor


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str
    city_name: str
    location_coordinate: str
    search_language: str
    search_depth: int
    categories: tuple[str, ...]
    min_rating: float
    min_reviews: int
    company_name: str
    company_contact: str
    price_total: float
    installments: int
    delivery_days: int
    validity_days: int
    output_dir: Path

    @classmethod
    def from_env(cls, require_api_key: bool = True) -> "Config":
        # override=False: uma variável já definida no terminal vence o arquivo .env.
        load_dotenv(RAIZ_PROJETO / ".env", override=False)

        api_key = _texto("AISA_API_KEY", "")
        if api_key == "cole-sua-chave-aqui":
            api_key = ""
        if require_api_key and not api_key:
            raise ConfigError(
                "AISA_API_KEY não configurada. Copie .env.example para .env e cole sua chave "
                "da AIsa (console em https://aisa.one). Para testar sem chave, use --demo --sem-llm."
            )

        categorias = tuple(c.strip() for c in _texto("CATEGORIES", "padaria").split(",") if c.strip())
        if not categorias:
            raise ConfigError("CATEGORIES está vazia. Informe ao menos uma categoria (ex.: padaria).")

        saida = Path(_texto("OUTPUT_DIR", "output"))
        if not saida.is_absolute():
            saida = RAIZ_PROJETO / saida

        return cls(
            api_key=api_key,
            base_url=_texto("AISA_BASE_URL", "https://api.aisa.one").rstrip("/"),
            model=_texto("AISA_MODEL", "gpt-4.1"),
            city_name=_texto("CITY_NAME", "Bragança Paulista"),
            location_coordinate=_texto("LOCATION_COORDINATE", "-22.9527,-46.5419,13z"),
            search_language=_texto("SEARCH_LANGUAGE", "pt"),
            search_depth=_inteiro("SEARCH_DEPTH", 40, 1, 700),
            categories=categorias,
            min_rating=_decimal("MIN_RATING", 4.5, 0.0, 5.0),
            min_reviews=_inteiro("MIN_REVIEWS", 10, 0, 1_000_000),
            company_name=_texto("COMPANY_NAME", "Sua Consultoria"),
            company_contact=_texto("COMPANY_CONTACT", ""),
            price_total=_decimal("PRICE_TOTAL", 2500.0, 1.0, 10_000_000.0),
            installments=_inteiro("INSTALLMENTS", 3, 1, 24),
            delivery_days=_inteiro("DELIVERY_DAYS", 15, 1, 365),
            validity_days=_inteiro("PROPOSAL_VALIDITY_DAYS", 7, 1, 365),
            output_dir=saida,
        )
