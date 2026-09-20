"""Ponto de entrada para a Vercel (e para qualquer servidor ASGI): expõe a variável `app`.

A Vercel procura um FastAPI chamado `app` em app.py, index.py, server.py ou main.py na raiz.
Por isso o CLI não usa mais um main.py na raiz: rode-o com `python -m prospector`.

IMPORTANTE: `app = ...` precisa ficar no nível mais externo do arquivo (sem estar dentro de um
try/except ou função) — a Vercel detecta a instância do FastAPI com uma checagem estática antes
de rodar o código, e não reconhece uma atribuição indentada. Por isso a lógica de tratamento de
erro mora em `_montar_app()`, e só a chamada final fica no nível externo.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _montar_app():
    try:
        from webapp.main import criar_app

        return criar_app()
    except Exception as erro:
        # Falta ou está errada uma variável de ambiente obrigatória (Firebase, ALLOWED_EMAILS, etc.).
        # Sem isto, a Vercel mostra um 500 genérico ("FUNCTION_INVOCATION_FAILED") sem nenhuma pista;
        # aqui a mensagem aparece direto na página, e o traceback completo vai para os Runtime Logs.
        from fastapi import FastAPI
        from fastapi.responses import PlainTextResponse

        log.exception("Falha ao iniciar o app: configuração ausente ou inválida.")
        mensagem = str(erro)

        app_com_erro = FastAPI()

        @app_com_erro.api_route("/{caminho:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        def _erro_de_configuracao(caminho: str = "") -> PlainTextResponse:
            return PlainTextResponse(
                "Erro de configuração do servidor:\n\n"
                f"{mensagem}\n\n"
                "Confira as variáveis de ambiente em Project Settings > Environment Variables na Vercel "
                "e refaça o deploy (Deployments > ⋯ > Redeploy).",
                status_code=500,
            )

        return app_com_erro


app = _montar_app()
