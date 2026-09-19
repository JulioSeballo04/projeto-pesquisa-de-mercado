"""Texto da proposta comercial: LLM (via AIsa) com plano B determinístico.

Regra de ouro: o LLM só escreve a parte persuasiva e usa SOMENTE fatos reais vindos do
Google Maps. Preço, prazo e itens do escopo são definidos aqui no código — assim a
proposta nunca promete um valor, um resultado ou uma estatística inventada.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import ROUND_DOWN, Decimal

from .aisa_client import AisaClient, AisaError
from .config import Config
from .models import Business, Proposal

log = logging.getLogger(__name__)

# Itens do escopo fixos (nomes) com uma descrição padrão. O LLM pode personalizar a descrição.
ESCOPO_PADRAO: list[tuple[str, str]] = [
    (
        "Página Inicial",
        "Página de apresentação com a identidade do negócio, endereço, horários, mapa e botões de contato, "
        "pensada para funcionar bem no celular.",
    ),
    (
        "Catálogo de Produtos",
        "Vitrine online com fotos, descrições e categorias dos produtos ou serviços, fácil de manter atualizada.",
    ),
    (
        "Integração WhatsApp",
        "Botão de WhatsApp em todas as páginas, com mensagem pré-preenchida para facilitar pedidos e orçamentos.",
    ),
    (
        "SEO Local",
        "Configuração para aparecer melhor em buscas da região, com títulos, descrições e dados estruturados, "
        "integrada ao perfil do Google do negócio.",
    ),
]

MAX_TEXTO = 320  # limite de caracteres por item, para o PDF não estourar o layout


# ---------- Investimento ----------


def formatar_brl(valor: float | Decimal) -> str:
    """2500 -> 'R$ 2.500,00' (sem depender do idioma configurado no computador)."""
    centavos = Decimal(str(valor)).quantize(Decimal("0.01"))
    inteiro, _, fracao = f"{centavos:.2f}".partition(".")
    com_milhar = f"{int(inteiro):,}".replace(",", ".")
    return f"R$ {com_milhar},{fracao}"


def tabela_investimento(cfg: Config) -> dict:
    """Valor total, parcela (truncada no centavo) e prazo."""
    total = Decimal(str(cfg.price_total)).quantize(Decimal("0.01"))
    parcela = (total / cfg.installments).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return {
        "total": total,
        "parcelas": cfg.installments,
        "parcela": parcela,
        "total_texto": formatar_brl(total),
        "parcela_texto": formatar_brl(parcela),
        "prazo_dias": cfg.delivery_days,
        "validade_dias": cfg.validity_days,
    }


# ---------- Prompt ----------

SISTEMA = (
    "Você é consultor comercial de uma consultoria de presença digital em Bragança Paulista/SP. "
    "Escreve propostas em português do Brasil, com tom profissional, próximo e respeitoso, sem exageros.\n"
    "REGRAS OBRIGATÓRIAS:\n"
    "1. Use SOMENTE os fatos fornecidos pelo usuário. Não invente números, porcentagens de aumento de vendas, "
    "prêmios, concorrentes, resultados garantidos nem informações que não estejam nos dados.\n"
    "2. Não cite preço, prazo nem forma de pagamento (isso é definido à parte).\n"
    "3. Seja específico ao tipo de negócio, mas sem afirmar coisas que você não sabe (ex.: não diga quais produtos "
    "a empresa vende).\n"
    "4. Responda APENAS com um objeto JSON válido, sem texto antes ou depois, sem markdown."
)


def montar_mensagens(negocio: Business, cfg: Config) -> list[dict]:
    # Minimização de dados: o LLM não precisa de telefone nem de endereço completo.
    fatos = {
        "nome": negocio.name,
        "categoria": negocio.category or "não informada",
        "cidade": cfg.city_name,
        "nota_google": negocio.rating,
        "quantidade_avaliacoes": negocio.reviews,
        "possui_site_proprio": False,
        "redes_sociais_encontradas": negocio.social_links or "nenhuma encontrada nos dados públicos",
        "perfil_google_reivindicado_pelo_dono": negocio.is_claimed,
        "horario_de_funcionamento_cadastrado": negocio.has_hours,
    }
    itens = [nome for nome, _ in ESCOPO_PADRAO]
    formato = {
        "resumo_diagnostico": "2 a 3 frases resumindo a presença digital atual, usando só os fatos dados",
        "pontos_fortes": ["2 a 4 itens curtos, baseados nos fatos"],
        "pontos_fracos": ["2 a 4 itens curtos sobre a falta de site próprio e suas consequências práticas"],
        "escopo": {item: "1 frase personalizada para este tipo de negócio" for item in itens},
        "argumentos": ["3 a 5 argumentos persuasivos, sem números inventados"],
        "chamada_final": "1 a 2 frases convidando o dono a conversar (sem pressão)",
    }
    usuario = (
        "Fatos verificados do negócio (Google Maps):\n"
        f"{json.dumps(fatos, ensure_ascii=False, indent=2)}\n\n"
        "Escreva a proposta de criação de site profissional neste formato JSON exato:\n"
        f"{json.dumps(formato, ensure_ascii=False, indent=2)}"
    )
    return [{"role": "system", "content": SISTEMA}, {"role": "user", "content": usuario}]


# ---------- Leitura e validação da resposta do LLM ----------

_CERCA_MARKDOWN = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extrair_json(texto: str) -> dict:
    """Aceita JSON puro, JSON dentro de ```cercas``` ou com texto ao redor."""
    limpo = _CERCA_MARKDOWN.sub("", texto.strip()).strip()
    try:
        dados = json.loads(limpo)
    except json.JSONDecodeError:
        inicio, fim = limpo.find("{"), limpo.rfind("}")
        if inicio == -1 or fim <= inicio:
            raise ValueError("resposta sem objeto JSON") from None
        try:
            dados = json.loads(limpo[inicio : fim + 1])
        except json.JSONDecodeError as erro:
            raise ValueError(f"JSON inválido: {erro.msg}") from None
    if not isinstance(dados, dict):
        raise ValueError("o JSON não é um objeto")
    return dados


def _texto(valor, obrigatorio: bool = True) -> str:
    if not isinstance(valor, str) or not valor.strip():
        if obrigatorio:
            raise ValueError("campo de texto ausente ou vazio")
        return ""
    return " ".join(valor.split())[:MAX_TEXTO]


def _lista(valor, minimo: int, maximo: int) -> list[str]:
    if not isinstance(valor, list):
        raise ValueError("campo deveria ser uma lista")
    itens = [_texto(v, obrigatorio=False) for v in valor]
    itens = [i for i in itens if i][:maximo]
    if len(itens) < minimo:
        raise ValueError(f"lista com menos de {minimo} itens")
    return itens


def validar_proposta(dados: dict) -> Proposal:
    """Confere o formato e limita tamanhos. Levanta ValueError se algo essencial faltar."""
    escopo_llm = dados.get("escopo") if isinstance(dados.get("escopo"), dict) else {}
    escopo = []
    for nome, descricao_padrao in ESCOPO_PADRAO:
        personalizado = _texto(escopo_llm.get(nome), obrigatorio=False)
        escopo.append((nome, personalizado or descricao_padrao))

    return Proposal(
        summary=_texto(dados.get("resumo_diagnostico")),
        strengths=_lista(dados.get("pontos_fortes"), 1, 4),
        weaknesses=_lista(dados.get("pontos_fracos"), 1, 4),
        scope=escopo,
        arguments=_lista(dados.get("argumentos"), 2, 5),
        closing=_texto(dados.get("chamada_final")),
        source="llm",
    )


# ---------- Plano B (sem LLM) ----------


def proposta_padrao(negocio: Business, cfg: Config) -> Proposal:
    """Texto genérico e seguro, montado só com os fatos. Usado se o LLM falhar ou com --sem-llm."""
    nota = f"{negocio.rating:.1f}".replace(".", ",") if negocio.rating is not None else "—"
    avaliacoes = negocio.reviews or 0
    redes = ", ".join(negocio.social_links)

    resumo = (
        f"{negocio.name} tem nota {nota} no Google, com {avaliacoes} avaliações, o que mostra clientes satisfeitos. "
        f"Mesmo assim, não encontramos um site próprio nos dados públicos do Google Maps"
        + (f"; a presença digital se apoia em {redes}." if redes else ".")
    )
    fortes = [
        f"Reputação sólida: nota {nota} no Google.",
        f"Prova social: {avaliacoes} avaliações de clientes reais.",
    ]
    if negocio.phone:
        fortes.append("Telefone de contato disponível no perfil do Google.")
    if redes:
        fortes.append(f"Já existe presença em {redes}.")

    fracos = [
        "Sem site próprio, quem pesquisa só encontra o perfil do Google, com pouco espaço para mostrar produtos e diferenciais.",
        "Informações como catálogo, novidades e formas de pedir ficam limitadas ao que cabe no perfil.",
        "O negócio depende de plataformas de terceiros para ser encontrado e apresentado.",
    ]
    argumentos = [
        "Seus clientes já avaliam bem o negócio: um site transforma essa reputação em mais contatos.",
        f"Quem busca {negocio.category.lower() or 'este tipo de negócio'} em {cfg.city_name} compara opções pelo celular; "
        "um site dá mais informações para a pessoa decidir por você.",
        "Um endereço digital próprio é seu: não muda com as regras das redes sociais.",
        "Com o botão de WhatsApp, o cliente chega direto ao seu atendimento.",
    ]
    return Proposal(
        summary=resumo,
        strengths=fortes,
        weaknesses=fracos,
        scope=list(ESCOPO_PADRAO),
        arguments=argumentos,
        closing="Posso apresentar um exemplo do site para o seu negócio, sem compromisso. Vamos conversar?",
        source="padrao",
    )


# ---------- Ponto de entrada ----------


def gerar_proposta(cliente: AisaClient | None, negocio: Business, cfg: Config, usar_llm: bool = True) -> Proposal:
    """Tenta o LLM (até 2 vezes); se falhar, cai no texto padrão em vez de perder o lead."""
    if not usar_llm or cliente is None:
        return proposta_padrao(negocio, cfg)

    mensagens = montar_mensagens(negocio, cfg)
    texto = ""
    for tentativa in (1, 2):
        try:
            texto = cliente.conversar(cfg.model, mensagens)
            return validar_proposta(extrair_json(texto))
        except ValueError as erro:
            log.warning("  Resposta do LLM fora do formato (%s) — tentativa %d/2.", erro, tentativa)
            mensagens = mensagens + [
                {"role": "assistant", "content": texto},
                {"role": "user", "content": "Sua resposta não seguiu o formato. Responda SOMENTE com o JSON pedido."},
            ]
        except AisaError as erro:
            if erro.status in (401, 402, 403, 404):
                raise  # erro de conta/rota: repetir ou usar texto padrão só esconderia o problema
            log.warning("  Falha ao chamar o LLM: %s", erro)
            break

    log.warning("  Usando o texto padrão para '%s'.", negocio.name)
    return proposta_padrao(negocio, cfg)
