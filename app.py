"""Ponto de entrada para a Vercel (e para qualquer servidor ASGI): expõe a variável `app`.

A Vercel procura um FastAPI chamado `app` em app.py, index.py, server.py ou main.py na raiz.
Por isso o CLI não usa mais um main.py na raiz: rode-o com `python -m prospector`.
"""

from webapp.main import criar_app

app = criar_app()
