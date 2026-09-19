"""Cliente HTTP da AIsa: um único header (Authorization: Bearer) para tudo.

Rotas usadas (conforme a documentação pública da AIsa):
  - Google Maps (via DataForSEO): POST /apis/v1/dataforseo/serp/google/maps/live/advanced
  - LLM (formato OpenAI):         POST /v1/chat/completions
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# (segundos para conectar, segundos para receber a resposta). A busca "live" do Maps
# pode demorar, por isso a leitura tem um prazo generoso.
TIMEOUT = (10, 90)


class AisaError(Exception):
    """Falha ao falar com a AIsa. A mensagem já vem em português e sem dados sensíveis."""

    def __init__(self, mensagem: str, status: int | None = None):
        super().__init__(mensagem)
        self.status = status


_MENSAGENS_HTTP = {
    401: "Chave da AIsa inválida ou ausente. Confira AISA_API_KEY no arquivo .env.",
    402: "A AIsa recusou a chamada por saldo/crédito. Verifique seu saldo no console da AIsa.",
    403: "Sem permissão para este recurso com a chave atual. Verifique o plano/permissões no console da AIsa.",
    404: "Rota não encontrada na AIsa. Ela pode ter mudado; confira a documentação em https://aisa.one/docs.",
    429: "Limite de requisições da AIsa atingido. Aguarde um pouco e tente de novo.",
}


class AisaClient:
    def __init__(self, api_key: str, base_url: str = "https://api.aisa.one", session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        if session is None:
            # Retentativas só para "muitas requisições" (429) e indisponibilidade momentânea
            # (502/503/504). NÃO repetimos em erro 500 nem em timeout: a busca do Maps é cobrada
            # por chamada e repetir às cegas poderia cobrar duas vezes.
            retry = Retry(
                total=2,
                backoff_factor=2.0,
                status_forcelist=(429, 502, 503, 504),
                allowed_methods=frozenset({"POST"}),
                respect_retry_after_header=True,
                raise_on_status=False,
            )
            self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def post(self, path: str, corpo: Any) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resposta = self.session.post(url, json=corpo, timeout=TIMEOUT)
        except requests.exceptions.Timeout:
            raise AisaError("A AIsa demorou demais para responder (timeout). Tente novamente.") from None
        except requests.exceptions.ConnectionError:
            raise AisaError("Não foi possível conectar à AIsa. Verifique sua internet.") from None
        except requests.exceptions.RequestException as erro:
            raise AisaError(f"Falha de rede ao chamar a AIsa: {type(erro).__name__}.") from None

        if resposta.status_code >= 400:
            base = _MENSAGENS_HTTP.get(resposta.status_code, f"A AIsa respondeu com erro HTTP {resposta.status_code}.")
            # Só um trecho curto do corpo, para diagnóstico; o header (com a chave) nunca é exibido.
            trecho = resposta.text.strip().replace("\n", " ")[:200]
            detalhe = f" Detalhe: {trecho}" if trecho else ""
            raise AisaError(base + detalhe, status=resposta.status_code)

        try:
            dados = resposta.json()
        except ValueError:
            raise AisaError("A AIsa devolveu uma resposta que não é JSON válido.", status=resposta.status_code) from None
        if not isinstance(dados, dict):
            raise AisaError("A AIsa devolveu um JSON em formato inesperado.", status=resposta.status_code)
        return dados

    # ---------- Chamadas de alto nível ----------

    def buscar_maps(self, palavra_chave: str, coordenada: str, idioma: str, profundidade: int) -> list[dict]:
        """Busca no Google Maps e devolve a lista bruta de itens (dicts do DataForSEO)."""
        corpo = [
            {
                "keyword": palavra_chave,
                "location_coordinate": coordenada,
                "language_code": idioma,
                "depth": profundidade,
            }
        ]
        dados = self.post("/apis/v1/dataforseo/serp/google/maps/live/advanced", corpo)
        return extrair_itens_maps(dados)

    def conversar(self, modelo: str, mensagens: list[dict], temperatura: float = 0.4, max_tokens: int = 1800) -> str:
        """Chama o LLM (chat/completions, formato OpenAI) e devolve o texto da resposta."""
        dados = self.post(
            "/v1/chat/completions",
            {"model": modelo, "messages": mensagens, "temperature": temperatura, "max_tokens": max_tokens},
        )
        try:
            conteudo = dados["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AisaError("Resposta do LLM sem o campo choices[0].message.content.") from None
        if isinstance(conteudo, list):  # alguns provedores devolvem blocos de texto
            conteudo = "".join(bloco.get("text", "") for bloco in conteudo if isinstance(bloco, dict))
        if not isinstance(conteudo, str) or not conteudo.strip():
            raise AisaError("O LLM devolveu uma resposta vazia.")
        return conteudo


def extrair_itens_maps(dados: dict) -> list[dict]:
    """Abre o "envelope" do DataForSEO: tasks[0].result[0].items.

    Códigos 20000 = sucesso. Qualquer outro vira um erro legível.
    """
    codigo = dados.get("status_code")
    if codigo not in (None, 20000):
        raise AisaError(f"Erro do provedor de dados: {dados.get('status_message', 'sem detalhe')} (código {codigo}).")

    tarefas = dados.get("tasks") or []
    if not tarefas:
        raise AisaError("Resposta da busca sem a lista 'tasks'.")
    tarefa = tarefas[0]
    codigo_tarefa = tarefa.get("status_code")
    if codigo_tarefa not in (None, 20000):
        raise AisaError(f"Busca recusada: {tarefa.get('status_message', 'sem detalhe')} (código {codigo_tarefa}).")

    resultados = tarefa.get("result") or []
    if not resultados or not isinstance(resultados[0], dict):
        return []  # busca válida, mas sem resultados
    return [item for item in (resultados[0].get("items") or []) if isinstance(item, dict)]
