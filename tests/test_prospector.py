"""Testes do Prospector. Rodam sem internet e sem chave da AIsa (tudo com dados falsos).

    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import requests

from prospector.aisa_client import AisaClient, AisaError, extrair_itens_maps
from prospector.cli import main as cli_main
from prospector.config import Config, ConfigError
from prospector.demo_data import ITENS_DEMO
from prospector.models import Business
from prospector.pdf_export import gerar_pdf, texto_seguro
from prospector.places import buscar_leads, dominio_raiz, interpretar_item, slug, triar
from prospector.proposal import (
    extrair_json,
    formatar_brl,
    gerar_proposta,
    proposta_padrao,
    tabela_investimento,
    validar_proposta,
)


def config_teste(**mudancas) -> Config:
    base = dict(
        api_key="chave-de-teste", base_url="https://api.aisa.one", model="modelo-x",
        city_name="Bragança Paulista", location_coordinate="-22.9527,-46.5419,13z",
        search_language="pt", search_depth=20, categories=("padaria",), min_rating=4.5,
        min_reviews=10, company_name="Consultoria Teste", company_contact="WhatsApp (11) 90000-0000",
        price_total=2500.0, installments=3, delivery_days=15, validity_days=7,
        output_dir=Path(tempfile.gettempdir()),
    )
    base.update(mudancas)
    return Config(**base)


def negocio_teste(**mudancas) -> Business:
    base = dict(
        place_id="p1", name="Padaria Teste", category="Padaria",
        address="Rua A, 1 - Centro, Bragança Paulista - SP", phone="(11) 90000-0001",
        rating=4.8, reviews=200, website_domain=None, website_url=None,
    )
    base.update(mudancas)
    return Business(**base)


def item_maps(**campos) -> dict:
    base = {
        "type": "maps_search", "title": "Padaria Teste", "category": "Padaria", "place_id": "p1",
        "address": "Rua A, 1 - Centro, Bragança Paulista - SP", "phone": "(11) 90000-0001",
        "rating": {"value": 4.8, "votes_count": 200}, "domain": None, "url": None,
    }
    base.update(campos)
    return base


class FalsaResposta:
    def __init__(self, status=200, corpo=None, texto=None):
        self.status_code = status
        self._corpo = corpo
        self.text = texto if texto is not None else json.dumps(corpo or {})

    def json(self):
        if self._corpo is None:
            raise ValueError("sem json")
        return self._corpo


class FalsaSessao:
    def __init__(self, resposta=None, erro=None):
        self.headers = {}
        self.resposta = resposta
        self.erro = erro
        self.chamadas = []

    def post(self, url, json=None, timeout=None):
        self.chamadas.append((url, json))
        if self.erro:
            raise self.erro
        return self.resposta


class ClienteFalso:
    """Substitui o AisaClient nos testes da proposta."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def conversar(self, modelo, mensagens, **kw):
        self.chamadas += 1
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


PROPOSTA_JSON = {
    "resumo_diagnostico": "Boa reputação, mas sem site.",
    "pontos_fortes": ["Nota alta", "Muitas avaliações"],
    "pontos_fracos": ["Sem site próprio"],
    "escopo": {"Página Inicial": "Página inicial da padaria."},
    "argumentos": ["Argumento um", "Argumento dois", "Argumento três"],
    "chamada_final": "Vamos conversar?",
}


class TestTriagem(unittest.TestCase):
    def test_dominio_raiz(self):
        self.assertEqual(dominio_raiz("https://www.Exemplo.com.br/loja?x=1"), "exemplo.com.br")
        self.assertEqual(dominio_raiz("instagram.com"), "instagram.com")
        self.assertIsNone(dominio_raiz(""))
        self.assertIsNone(dominio_raiz(None))

    def test_instagram_nao_conta_como_site(self):
        n = interpretar_item(item_maps(domain="instagram.com", url="https://www.instagram.com/loja"))
        self.assertIsNone(n.website_domain)
        self.assertEqual(n.social_links, ["Instagram"])
        self.assertTrue(triar(n, config_teste())[0])

    def test_pagina_do_google_nao_conta_como_site(self):
        n = interpretar_item(item_maps(domain="business.site", url="https://loja.business.site"))
        self.assertIsNone(n.website_domain)
        self.assertEqual(n.social_links, [])

    def test_site_proprio_descarta(self):
        n = interpretar_item(item_maps(domain="padaria.com.br", url="https://padaria.com.br"))
        self.assertEqual(triar(n, config_teste()), (False, "possui_site"))

    def test_dominio_do_site_derivado_da_url(self):
        n = interpretar_item(item_maps(domain=None, url="https://www.padaria.com.br/contato"))
        self.assertEqual(n.website_domain, "padaria.com.br")

    def test_motivos_de_descarte(self):
        cfg = config_teste()
        self.assertEqual(triar(negocio_teste(address="Rua X, Atibaia - SP"), cfg), (False, "fora_da_cidade"))
        self.assertEqual(triar(negocio_teste(rating=None), cfg), (False, "sem_nota"))
        self.assertEqual(triar(negocio_teste(rating=4.4), cfg), (False, "nota_baixa"))
        self.assertEqual(triar(negocio_teste(reviews=3), cfg), (False, "poucas_avaliacoes"))
        self.assertEqual(triar(negocio_teste(phone=None), cfg), (False, "sem_telefone"))
        self.assertEqual(triar(negocio_teste(phone=None), cfg, permitir_sem_telefone=True), (True, "ok"))
        self.assertEqual(triar(negocio_teste(), cfg), (True, "ok"))

    def test_nota_no_limite_e_aprovada(self):
        self.assertTrue(triar(negocio_teste(rating=4.5), config_teste())[0])

    def test_cidade_sem_acento_no_endereco(self):
        n = negocio_teste(address="Rua A, 1 - centro, braganca paulista - SP")
        self.assertTrue(triar(n, config_teste())[0])

    def test_ignora_itens_que_nao_sao_maps_search(self):
        self.assertIsNone(interpretar_item(item_maps(type="maps_paid_item")))
        self.assertIsNone(interpretar_item(item_maps(title="")))

    def test_endereco_montado_de_address_info(self):
        n = interpretar_item(item_maps(address=None, address_info={"address": "Rua B, 2", "city": "Bragança Paulista"}))
        self.assertIn("Bragança Paulista", n.address)

    def test_campos_malformados_nao_quebram(self):
        n = interpretar_item(item_maps(rating={"value": "abc", "votes_count": None}, is_claimed="sim"))
        self.assertIsNone(n.rating)
        self.assertIsNone(n.is_claimed)

    def test_duplicados_entre_categorias(self):
        itens = {"padaria": [item_maps()], "lanchonete": [item_maps()]}
        leads = buscar_leads(None, config_teste(), ["padaria", "lanchonete"], 10, itens_demo=itens)
        self.assertEqual(len(leads), 1)

    def test_dados_de_demo_cobrem_todas_as_regras(self):
        leads = buscar_leads(None, config_teste(), list(ITENS_DEMO), 10, itens_demo=ITENS_DEMO)
        motivos = sorted(l.reason for l in leads)
        self.assertEqual(
            motivos,
            sorted(["ok", "ok", "possui_site", "nota_baixa", "sem_telefone", "fora_da_cidade"]),
        )

    def test_slug(self):
        self.assertEqual(slug("Padaria São João & Filhos!"), "padaria-sao-joao-filhos")
        self.assertEqual(slug("!!!"), "empresa")


class TestClienteAisa(unittest.TestCase):
    def cliente(self, resposta=None, erro=None):
        sessao = FalsaSessao(resposta, erro)
        return AisaClient("segredo-123", session=sessao), sessao

    def test_envia_bearer_e_corpo_do_maps(self):
        envelope = {"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"items": [item_maps()]}]}]}
        cliente, sessao = self.cliente(FalsaResposta(200, envelope))
        itens = cliente.buscar_maps("padaria", "-22.95,-46.54,13z", "pt", 20)
        self.assertEqual(len(itens), 1)
        self.assertEqual(sessao.headers["Authorization"], "Bearer segredo-123")
        url, corpo = sessao.chamadas[0]
        self.assertTrue(url.endswith("/apis/v1/dataforseo/serp/google/maps/live/advanced"))
        self.assertEqual(corpo[0]["keyword"], "padaria")
        self.assertEqual(corpo[0]["depth"], 20)

    def test_erros_http_traduzidos_sem_vazar_a_chave(self):
        for status, trecho in [(401, "Chave da AIsa"), (402, "saldo"), (403, "permissão"), (404, "Rota"), (429, "Limite")]:
            cliente, _ = self.cliente(FalsaResposta(status, texto="detalhe do servidor"))
            with self.assertRaises(AisaError) as ctx:
                cliente.post("/x", {})
            self.assertIn(trecho, str(ctx.exception))
            self.assertEqual(ctx.exception.status, status)
            self.assertNotIn("segredo-123", str(ctx.exception))

    def test_erro_de_rede_e_timeout(self):
        cliente, _ = self.cliente(erro=requests.exceptions.Timeout())
        with self.assertRaisesRegex(AisaError, "timeout"):
            cliente.post("/x", {})
        cliente, _ = self.cliente(erro=requests.exceptions.ConnectionError())
        with self.assertRaisesRegex(AisaError, "conectar"):
            cliente.post("/x", {})

    def test_resposta_que_nao_e_json(self):
        cliente, _ = self.cliente(FalsaResposta(200, corpo=None, texto="<html>"))
        with self.assertRaisesRegex(AisaError, "JSON"):
            cliente.post("/x", {})

    def test_envelope_do_dataforseo(self):
        self.assertEqual(extrair_itens_maps({"tasks": [{"result": [{"items": []}]}]}), [])
        self.assertEqual(extrair_itens_maps({"tasks": [{"result": None}]}), [])  # busca sem resultados
        with self.assertRaisesRegex(AisaError, "Busca recusada"):
            extrair_itens_maps({"tasks": [{"status_code": 40501, "status_message": "Invalid Field"}]})
        with self.assertRaisesRegex(AisaError, "provedor"):
            extrair_itens_maps({"status_code": 40000, "status_message": "erro"})
        with self.assertRaisesRegex(AisaError, "tasks"):
            extrair_itens_maps({})

    def test_conversar_le_choices(self):
        cliente, _ = self.cliente(FalsaResposta(200, {"choices": [{"message": {"content": "olá"}}]}))
        self.assertEqual(cliente.conversar("m", []), "olá")
        cliente, _ = self.cliente(FalsaResposta(200, {"choices": []}))
        with self.assertRaisesRegex(AisaError, "choices"):
            cliente.conversar("m", [])
        cliente, _ = self.cliente(FalsaResposta(200, {"choices": [{"message": {"content": "  "}}]}))
        with self.assertRaisesRegex(AisaError, "vazia"):
            cliente.conversar("m", [])


class TestProposta(unittest.TestCase):
    def test_formatar_brl(self):
        self.assertEqual(formatar_brl(2500), "R$ 2.500,00")
        self.assertEqual(formatar_brl(Decimal("833.33")), "R$ 833,33")
        self.assertEqual(formatar_brl(1234567.5), "R$ 1.234.567,50")

    def test_investimento_parcelado(self):
        t = tabela_investimento(config_teste())
        self.assertEqual((t["total_texto"], t["parcelas"], t["parcela_texto"]), ("R$ 2.500,00", 3, "R$ 833,33"))
        self.assertEqual(t["prazo_dias"], 15)

    def test_extrair_json_variantes(self):
        self.assertEqual(extrair_json('{"a": 1}'), {"a": 1})
        self.assertEqual(extrair_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extrair_json('Claro! Aqui está:\n{"a": 1}\nEspero ter ajudado.'), {"a": 1})
        for ruim in ("sem json", "[1, 2]", '{"a": ', ""):
            with self.assertRaises(ValueError):
                extrair_json(ruim)

    def test_validar_completa_escopo_e_limita_tamanho(self):
        dados = dict(PROPOSTA_JSON, argumentos=["x" * 900, "b", "c", "d", "e", "f", "g"])
        p = validar_proposta(dados)
        self.assertEqual([nome for nome, _ in p.scope],
                         ["Página Inicial", "Catálogo de Produtos", "Integração WhatsApp", "SEO Local"])
        self.assertEqual(p.scope[0][1], "Página inicial da padaria.")  # personalizado pelo LLM
        self.assertTrue(p.scope[1][1])  # padrão, porque o LLM não mandou
        self.assertEqual(len(p.arguments), 5)
        self.assertLessEqual(len(p.arguments[0]), 320)

    def test_validar_rejeita_resposta_incompleta(self):
        with self.assertRaises(ValueError):
            validar_proposta({"resumo_diagnostico": "x"})
        with self.assertRaises(ValueError):
            validar_proposta(dict(PROPOSTA_JSON, argumentos=["só um"]))

    def test_gerar_com_llm(self):
        cliente = ClienteFalso([json.dumps(PROPOSTA_JSON)])
        p = gerar_proposta(cliente, negocio_teste(), config_teste())
        self.assertEqual(p.source, "llm")

    def test_json_invalido_tenta_de_novo_e_depois_usa_padrao(self):
        cliente = ClienteFalso(["não é json", json.dumps(PROPOSTA_JSON)])
        self.assertEqual(gerar_proposta(cliente, negocio_teste(), config_teste()).source, "llm")
        self.assertEqual(cliente.chamadas, 2)
        cliente = ClienteFalso(["lixo", "mais lixo"])
        self.assertEqual(gerar_proposta(cliente, negocio_teste(), config_teste()).source, "padrao")

    def test_erro_transitorio_usa_padrao_mas_erro_de_conta_propaga(self):
        cliente = ClienteFalso([AisaError("instável", status=503)])
        self.assertEqual(gerar_proposta(cliente, negocio_teste(), config_teste()).source, "padrao")
        cliente = ClienteFalso([AisaError("chave inválida", status=401)])
        with self.assertRaises(AisaError):
            gerar_proposta(cliente, negocio_teste(), config_teste())

    def test_sem_llm_nao_chama_o_cliente(self):
        cliente = ClienteFalso([])
        self.assertEqual(gerar_proposta(cliente, negocio_teste(), config_teste(), usar_llm=False).source, "padrao")
        self.assertEqual(cliente.chamadas, 0)

    def test_prompt_nao_envia_telefone_nem_endereco(self):
        from prospector.proposal import montar_mensagens

        texto = json.dumps(montar_mensagens(negocio_teste(), config_teste()), ensure_ascii=False)
        self.assertNotIn("90000-0001", texto)
        self.assertNotIn("Rua A", texto)
        self.assertIn("Padaria Teste", texto)


class TestPDF(unittest.TestCase):
    def test_texto_seguro(self):
        self.assertEqual(texto_seguro("Ação – “ótima” • 🍞 ok…"), 'Ação - "ótima" - ok...')

    def test_gera_pdf_valido_com_caracteres_problematicos(self):
        n = negocio_teste(name="Padaria “Pão & Cia” — Ünico 🍞", social_links=["Instagram"])
        p = validar_proposta(dict(PROPOSTA_JSON, resumo_diagnostico="Texto com emoji 🎉 e travessão — ok."))
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = gerar_pdf(n, p, config_teste(), Path(pasta) / "sub" / "proposta.pdf", hoje=date(2026, 9, 19))
            dados = arquivo.read_bytes()
        self.assertTrue(dados.startswith(b"%PDF"))
        self.assertGreater(len(dados), 2000)

    def test_pdf_com_textos_longos_nao_quebra(self):
        n = negocio_teste(name="N" * 150, address="E" * 400)
        p = proposta_padrao(n, config_teste())
        with tempfile.TemporaryDirectory() as pasta:
            gerar_pdf(n, p, config_teste(price_total=1234567.89, installments=1), Path(pasta) / "x.pdf")


class TestConfigECLI(unittest.TestCase):
    def ambiente(self, **variaveis):
        return mock.patch.dict(os.environ, variaveis, clear=True)

    def test_config_exige_chave(self):
        with self.ambiente(), mock.patch("prospector.config.load_dotenv"):
            with self.assertRaisesRegex(ConfigError, "AISA_API_KEY"):
                Config.from_env()
            self.assertEqual(Config.from_env(require_api_key=False).api_key, "")

    def test_placeholder_do_exemplo_conta_como_sem_chave(self):
        with self.ambiente(AISA_API_KEY="cole-sua-chave-aqui"), mock.patch("prospector.config.load_dotenv"):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_config_valida_numeros(self):
        with self.ambiente(AISA_API_KEY="k", MIN_RATING="9"), mock.patch("prospector.config.load_dotenv"):
            with self.assertRaisesRegex(ConfigError, "MIN_RATING"):
                Config.from_env()
        with self.ambiente(AISA_API_KEY="k", SEARCH_DEPTH="muito"), mock.patch("prospector.config.load_dotenv"):
            with self.assertRaisesRegex(ConfigError, "SEARCH_DEPTH"):
                Config.from_env()

    def test_config_aceita_virgula_decimal(self):
        with self.ambiente(AISA_API_KEY="k", MIN_RATING="4,7"), mock.patch("prospector.config.load_dotenv"):
            self.assertEqual(Config.from_env().min_rating, 4.7)

    def rodar_cli(self, argumentos, **variaveis):
        saida = io.StringIO()
        with self.ambiente(**variaveis), mock.patch("prospector.config.load_dotenv"), \
                mock.patch("logging.basicConfig"), contextlib.redirect_stdout(saida):
            codigo = cli_main(argumentos)
        return codigo, saida.getvalue()

    def test_cli_demo_completo(self):
        with tempfile.TemporaryDirectory() as pasta:
            codigo, saida = self.rodar_cli(["--demo", "--sem-llm", "--saida", pasta])
            self.assertEqual(codigo, 0, saida)
            csvs = list(Path(pasta).glob("*/leads.csv"))
            pdfs = list(Path(pasta).glob("*/propostas/*.pdf"))
            self.assertEqual((len(csvs), len(pdfs)), (1, 2))
            conteudo = csvs[0].read_text(encoding="utf-8-sig")
            self.assertIn("já possui site próprio", conteudo)
            self.assertIn("aprovado", conteudo)
            self.assertEqual(len(conteudo.strip().splitlines()), 1 + 6)  # cabeçalho + 6 estabelecimentos

    def test_cli_dry_run_nao_gera_pdf(self):
        with tempfile.TemporaryDirectory() as pasta:
            codigo, saida = self.rodar_cli(["--demo", "--dry-run", "--saida", pasta])
            self.assertEqual(codigo, 0)
            self.assertEqual(list(Path(pasta).glob("*/propostas/*.pdf")), [])
            self.assertEqual(len(list(Path(pasta).glob("*/leads.csv"))), 1)

    def test_cli_limite(self):
        with tempfile.TemporaryDirectory() as pasta:
            self.rodar_cli(["--demo", "--sem-llm", "--limite", "1", "--saida", pasta])
            self.assertEqual(len(list(Path(pasta).glob("*/propostas/*.pdf"))), 1)

    def test_cli_sem_chave_explica_o_que_fazer(self):
        codigo, saida = self.rodar_cli(["-c", "padaria", "--yes"])
        self.assertEqual(codigo, 1)
        self.assertIn("AISA_API_KEY", saida)

    def test_cli_nenhum_lead_aprovado(self):
        with tempfile.TemporaryDirectory() as pasta:
            codigo, saida = self.rodar_cli(["--demo", "--sem-llm", "--min-nota", "5", "--min-avaliacoes", "99999", "--saida", pasta])
            self.assertEqual(codigo, 2)
            self.assertIn("Nenhum lead aprovado", saida)

    def test_cli_valida_argumentos(self):
        self.assertEqual(self.rodar_cli(["--demo", "--sem-llm", "--min-nota", "7"])[0], 1)
        self.assertEqual(self.rodar_cli(["--demo", "--sem-llm", "--limite", "0"])[0], 1)

    def test_cli_busca_real_com_api_falsa(self):
        """Fluxo completo com a AIsa simulada: busca, triagem, LLM e PDF."""
        envelope = {"tasks": [{"status_code": 20000, "result": [{"items": [item_maps(), item_maps(place_id="p2", title="Outra Padaria", domain="outra.com.br")]}]}]}
        chat = {"choices": [{"message": {"content": json.dumps(PROPOSTA_JSON)}}]}

        def post(url, json=None, timeout=None):
            return FalsaResposta(200, envelope if "dataforseo" in url else chat)

        sessao_falsa = SimpleNamespace(headers={}, post=post, mount=lambda *a, **k: None)
        with tempfile.TemporaryDirectory() as pasta, mock.patch("requests.Session", return_value=sessao_falsa):
            codigo, saida = self.rodar_cli(["-c", "padaria", "--yes", "--saida", pasta], AISA_API_KEY="chave-falsa")
            self.assertEqual(codigo, 0, saida)
            self.assertEqual(len(list(Path(pasta).glob("*/propostas/*.pdf"))), 1)
            self.assertIn("já possui site próprio", saida)

    def test_cli_erro_de_conta_interrompe_mas_salva_a_planilha(self):
        envelope = {"tasks": [{"status_code": 20000, "result": [{"items": [item_maps()]}]}]}

        def post(url, json=None, timeout=None):
            return FalsaResposta(200, envelope) if "dataforseo" in url else FalsaResposta(401, texto="unauthorized")

        sessao_falsa = SimpleNamespace(headers={}, post=post, mount=lambda *a, **k: None)
        with tempfile.TemporaryDirectory() as pasta, mock.patch("requests.Session", return_value=sessao_falsa):
            codigo, saida = self.rodar_cli(["-c", "padaria", "--yes", "--saida", pasta], AISA_API_KEY="chave-falsa")
            self.assertEqual(codigo, 1)
            self.assertIn("Chave da AIsa", saida)
            self.assertEqual(len(list(Path(pasta).glob("*/leads.csv"))), 1)


if __name__ == "__main__":
    unittest.main()
