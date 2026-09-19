"""Autenticação: o navegador faz login no Firebase e manda o token; aqui ele é conferido.

O servidor NUNCA vê a senha. Ele só recebe o ID token (um JWT assinado pelo Google), confere
a assinatura e o projeto e, por fim, verifica se o e-mail está na lista permitida.
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import HTTPException, Request

from .settings import WebSettings

log = logging.getLogger(__name__)

Verificador = Callable[[str], str]  # token -> e-mail


def criar_verificador_firebase(project_id: str) -> Verificador:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    requisicao = google_requests.Request()

    def verificar(token: str) -> str:
        # Confere assinatura, validade, emissor e que o token é DESTE projeto Firebase.
        dados = id_token.verify_firebase_token(token, requisicao, audience=project_id)
        return str(dados.get("email") or "").lower()

    return verificar


def obter_usuario(request: Request) -> str:
    """Dependência do FastAPI: devolve o e-mail do usuário ou responde 401/403."""
    settings: WebSettings = request.app.state.settings
    if settings.auth_mode == "none":
        return "local"

    cabecalho = request.headers.get("authorization", "")
    if not cabecalho.lower().startswith("bearer ") or len(cabecalho) < 20:
        raise HTTPException(status_code=401, detail="Faça login para continuar.")
    token = cabecalho[7:].strip()

    try:
        email = request.app.state.verificar_token(token)
    except Exception as erro:  # qualquer falha de verificação = não autenticado
        log.info("Token recusado: %s", type(erro).__name__)
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada. Entre novamente.") from None

    if not email or email not in settings.allowed_emails:
        log.warning("Login recusado (e-mail fora da lista permitida).")
        raise HTTPException(status_code=403, detail="Este e-mail não tem permissão para usar o app.")
    return email
