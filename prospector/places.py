"""Busca de estabelecimentos no Google Maps e triagem (nota, cidade, contato, site próprio)."""

from __future__ import annotations

import logging
import re
import unicodedata
from urllib.parse import urlparse

from .aisa_client import AisaClient
from .config import Config
from .models import Business, Lead

log = logging.getLogger(__name__)


def normalizar(texto: str | None) -> str:
    """Minúsculas e sem acentos: 'Bragança' == 'braganca'."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower().strip()


# Domínios que NÃO contam como "site próprio": redes sociais, mensageiros, agregadores de
# links, apps de delivery e páginas do próprio Google. Muitos negócios cadastram o Instagram
# no lugar do site — para a prospecção eles continuam sendo alvo.
DOMINIOS_NAO_SAO_SITE = {
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "fb.com": "Facebook",
    "fb.me": "Facebook",
    "wa.me": "WhatsApp",
    "whatsapp.com": "WhatsApp",
    "api.whatsapp.com": "WhatsApp",
    "tiktok.com": "TikTok",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "twitter.com": "X/Twitter",
    "x.com": "X/Twitter",
    "linkedin.com": "LinkedIn",
    "linktr.ee": "Linktree",
    "beacons.ai": "Beacons",
    "ifood.com.br": "iFood",
    "rappi.com.br": "Rappi",
    "99app.com": "99Food",
    "tripadvisor.com.br": "TripAdvisor",
    "tripadvisor.com": "TripAdvisor",
    "google.com": "Google",
    "google.com.br": "Google",
    "goo.gl": "Google",
    "g.page": "Google",
    "business.site": "Google",  # site gratuito antigo do Google Meu Negócio
    "maps.app.goo.gl": "Google",
}

# Só estas redes entram no texto do diagnóstico como "presença em redes sociais".
REDES_SOCIAIS = {"Instagram", "Facebook", "WhatsApp", "TikTok", "YouTube", "X/Twitter", "LinkedIn", "Linktree"}


def dominio_raiz(valor: str | None) -> str | None:
    """Extrai o domínio de uma URL ou de um domínio solto ('https://www.x.com/a' -> 'x.com')."""
    if not valor:
        return None
    texto = valor.strip().lower()
    if not texto:
        return None
    if "//" not in texto:
        texto = "//" + texto
    host = urlparse(texto).hostname or ""
    host = host.removeprefix("www.")
    return host or None


def _rotulo_nao_site(dominio: str) -> str | None:
    """Se o domínio é rede social/agregador/Google, devolve o nome; senão None."""
    for base, rotulo in DOMINIOS_NAO_SAO_SITE.items():
        if dominio == base or dominio.endswith("." + base):
            return rotulo
    return None


def _para_float(valor) -> float | None:
    try:
        return float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _para_int(valor) -> int | None:
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def interpretar_item(item: dict) -> Business | None:
    """Converte um item do DataForSEO (type = maps_search) em Business. None se não servir."""
    if item.get("type") not in (None, "maps_search"):
        return None  # ignora anúncios e outros tipos de bloco
    nome = (item.get("title") or "").strip()
    if not nome:
        return None

    endereco = (item.get("address") or "").strip()
    info = item.get("address_info") if isinstance(item.get("address_info"), dict) else {}
    if not endereco:
        partes = [info.get("address"), info.get("borough"), info.get("city"), info.get("region")]
        endereco = ", ".join(p for p in partes if p)
    cidade = info.get("city") or ""
    if cidade and normalizar(cidade) not in normalizar(endereco):
        endereco = f"{endereco}, {cidade}".strip(", ")

    avaliacao = item.get("rating") if isinstance(item.get("rating"), dict) else {}
    dominio = dominio_raiz(item.get("domain")) or dominio_raiz(item.get("url"))
    url_site = (item.get("url") or "").strip() or None

    redes: list[str] = []
    dominio_site: str | None = dominio
    if dominio:
        rotulo = _rotulo_nao_site(dominio)
        if rotulo:  # é rede social/agregador/Google: registra e trata como "sem site próprio"
            if rotulo in REDES_SOCIAIS:
                redes.append(rotulo)
            dominio_site = None

    cid = item.get("cid")
    telefone = (item.get("phone") or "").strip() or None
    # Identificador usado para não processar o mesmo local duas vezes.
    place_id = str(item.get("place_id") or cid or f"{nome}|{endereco}")

    return Business(
        place_id=place_id,
        name=nome,
        category=(item.get("category") or "").strip(),
        address=endereco,
        phone=telefone,
        rating=_para_float(avaliacao.get("value")),
        reviews=_para_int(avaliacao.get("votes_count")),
        website_domain=dominio_site,
        website_url=url_site if dominio_site else None,
        social_links=redes,
        is_claimed=item.get("is_claimed") if isinstance(item.get("is_claimed"), bool) else None,
        has_hours=bool(item.get("work_hours")),
        maps_url=f"https://www.google.com/maps?cid={cid}" if cid else None,
    )


def triar(negocio: Business, cfg: Config, permitir_sem_telefone: bool = False) -> tuple[bool, str]:
    """Decide se o negócio é um lead válido. Devolve (aprovado, motivo).

    A ordem importa: os descartes mais baratos e mais definitivos vêm primeiro.
    """
    if normalizar(cfg.city_name) not in normalizar(negocio.address):
        return False, "fora_da_cidade"
    if negocio.website_domain:
        return False, "possui_site"
    if negocio.rating is None:
        return False, "sem_nota"
    if negocio.rating < cfg.min_rating:
        return False, "nota_baixa"
    if (negocio.reviews or 0) < cfg.min_reviews:
        return False, "poucas_avaliacoes"
    if not negocio.phone and not permitir_sem_telefone:
        return False, "sem_telefone"
    return True, "ok"


def buscar_leads(
    cliente: AisaClient | None,
    cfg: Config,
    categorias: list[str],
    profundidade: int,
    itens_demo: dict[str, list[dict]] | None = None,
    permitir_sem_telefone: bool = False,
    progresso=None,
) -> list[Lead]:
    """Pesquisa cada categoria e devolve TODOS os estabelecimentos triados (aprovados e descartados).

    Guardar os descartados (com o motivo) deixa a planilha final auditável: dá para ver
    por que uma padaria conhecida ficou de fora.
    """
    vistos: set[str] = set()
    leads: list[Lead] = []

    for categoria in categorias:
        if itens_demo is not None:
            itens = itens_demo.get(categoria, [])
        else:
            assert cliente is not None
            log.info("Buscando '%s' em %s (até %d resultados)...", categoria, cfg.city_name, profundidade)
            if progresso:
                progresso(f"Buscando '{categoria}' no Google Maps...")
            itens = cliente.buscar_maps(categoria, cfg.location_coordinate, cfg.search_language, profundidade)
        log.info("  %d resultado(s) para '%s'.", len(itens), categoria)
        if progresso:
            progresso(f"'{categoria}': {len(itens)} resultado(s).")

        for item in itens:
            negocio = interpretar_item(item)
            if negocio is None:
                continue
            if negocio.place_id in vistos:
                continue  # o mesmo local aparece em mais de uma categoria
            vistos.add(negocio.place_id)
            aprovado, motivo = triar(negocio, cfg, permitir_sem_telefone)
            leads.append(Lead(business=negocio, approved=aprovado, reason=motivo, search_category=categoria))

    return leads


MOTIVOS_LEGIVEIS = {
    "ok": "aprovado",
    "fora_da_cidade": "endereço fora da cidade-alvo",
    "possui_site": "já possui site próprio",
    "sem_nota": "sem nota no Google",
    "nota_baixa": "nota abaixo do mínimo",
    "poucas_avaliacoes": "poucas avaliações",
    "sem_telefone": "sem telefone de contato",
}


def resumo_por_motivo(leads: list[Lead]) -> dict[str, int]:
    contagem: dict[str, int] = {}
    for lead in leads:
        contagem[lead.reason] = contagem.get(lead.reason, 0) + 1
    return contagem


_SO_LETRAS_NUMEROS = re.compile(r"[^a-z0-9]+")


def slug(texto: str, limite: int = 50) -> str:
    """'Padaria São João!' -> 'padaria-sao-joao' (para nomes de arquivo seguros)."""
    base = _SO_LETRAS_NUMEROS.sub("-", normalizar(texto)).strip("-")[:limite].strip("-")
    return base or "empresa"
