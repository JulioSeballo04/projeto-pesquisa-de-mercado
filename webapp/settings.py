"""Configurações específicas do app web (login, limites de custo, Firebase do front-end)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from prospector.config import RAIZ_PROJETO


class WebConfigError(Exception):
    """Configuração do app web ausente ou insegura (a mensagem diz o que corrigir)."""


def _inteiro(nome: str, padrao: int, minimo: int, maximo: int) -> int:
    bruto = (os.getenv(nome) or "").strip() or str(padrao)
    try:
        valor = int(bruto)
    except ValueError:
        raise WebConfigError(f"{nome} deve ser um número inteiro (valor atual: {bruto!r}).") from None
    if not minimo <= valor <= maximo:
        raise WebConfigError(f"{nome} deve estar entre {minimo} e {maximo} (valor atual: {valor}).")
    return valor


def _texto(nome: str) -> str:
    """Valor da variável; o texto de exemplo do .env.example ("cole-...") conta como não preenchido."""
    valor = (os.getenv(nome) or "").strip()
    return "" if valor.lower().startswith("cole-") else valor


@dataclass(frozen=True)
class WebSettings:
    auth_mode: str  # "firebase" (padrão, exige login) ou "none" (só para testar no seu computador)
    firebase_project_id: str
    firebase_api_key: str
    firebase_app_id: str
    firebase_auth_domain: str
    allowed_emails: frozenset[str]
    # Limites que protegem seus créditos, aplicados em CADA requisição (o servidor não guarda estado,
    # então o total por sessão é controlado pela página; aqui fica o teto de cada chamada):
    max_categories: int
    max_depth: int
    max_proposals: int

    @classmethod
    def from_env(cls) -> "WebSettings":
        load_dotenv(RAIZ_PROJETO / ".env", override=False)

        modo = (os.getenv("AUTH_MODE") or "firebase").strip().lower()
        if modo not in ("firebase", "none"):
            raise WebConfigError("AUTH_MODE deve ser 'firebase' ou 'none'.")

        projeto = _texto("FIREBASE_PROJECT_ID")
        emails = frozenset(e.strip().lower() for e in (os.getenv("ALLOWED_EMAILS") or "").split(",") if e.strip())

        if modo == "firebase":
            faltando = [
                nome for nome, valor in (
                    ("FIREBASE_PROJECT_ID", projeto),
                    ("FIREBASE_WEB_API_KEY", _texto("FIREBASE_WEB_API_KEY")),
                    ("FIREBASE_APP_ID", _texto("FIREBASE_APP_ID")),
                ) if not valor
            ]
            if faltando:
                raise WebConfigError(
                    f"Faltam no .env: {', '.join(faltando)}. Copie os valores do firebase-config.js do app de pesquisa "
                    "(ou use AUTH_MODE=none apenas para testar localmente)."
                )
            if not emails:
                # Sem lista, qualquer conta Firebase (ou seja, qualquer pessoa que consiga se cadastrar)
                # poderia gastar os créditos. Preferimos não subir.
                raise WebConfigError(
                    "ALLOWED_EMAILS está vazio. Informe os e-mails que podem usar o app, separados por vírgula."
                )

        return cls(
            auth_mode=modo,
            firebase_project_id=projeto,
            firebase_api_key=_texto("FIREBASE_WEB_API_KEY"),
            firebase_app_id=_texto("FIREBASE_APP_ID"),
            firebase_auth_domain=_texto("FIREBASE_AUTH_DOMAIN") or (f"{projeto}.firebaseapp.com" if projeto else ""),
            allowed_emails=emails,
            max_categories=_inteiro("MAX_CATEGORIES", 8, 1, 30),
            max_depth=_inteiro("MAX_DEPTH", 100, 1, 700),
            max_proposals=_inteiro("MAX_PROPOSALS", 15, 1, 100),
        )

    def firebase_public(self) -> dict | None:
        """Configuração pública do Firebase que o navegador precisa para fazer login."""
        if self.auth_mode != "firebase":
            return None
        return {
            "apiKey": self.firebase_api_key,
            "authDomain": self.firebase_auth_domain,
            "projectId": self.firebase_project_id,
            "appId": self.firebase_app_id,
        }
