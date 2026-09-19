"""Roda o servidor: python -m webapp

Por padrão só aceita conexões do próprio computador (127.0.0.1). Em uma hospedagem
(ou Docker), defina HOST=0.0.0.0 e a variável PORT que a plataforma fornece.
"""

from __future__ import annotations

import os
import sys

import uvicorn

from prospector.config import ConfigError
from webapp.main import criar_app
from webapp.settings import WebConfigError


def main() -> int:
    try:
        app = criar_app()
    except (WebConfigError, ConfigError) as erro:
        print(f"Erro de configuração: {erro}")
        return 1

    if app.state.settings.auth_mode == "none":
        print("ATENÇÃO: AUTH_MODE=none — o app está SEM login. Use somente no seu computador.")

    host = os.getenv("HOST", "127.0.0.1")
    porta = int(os.getenv("PORT", "8000"))
    if app.state.settings.auth_mode == "none" and host not in ("127.0.0.1", "localhost", "::1"):
        print("Recusado: AUTH_MODE=none só pode rodar com HOST=127.0.0.1. Ligue o login (AUTH_MODE=firebase).")
        return 1

    # workers=1: as tarefas ficam em memória, então precisa ser um único processo.
    uvicorn.run(app, host=host, port=porta, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
