"""Testes do app web (FastAPI, sem estado). Sem internet, sem chave e sem Firebase de verdade."""

from __future__ import annotations

import importlib
import io
import json
import os
import re
import sys
import unittest
import zipfile
from unittest import mock

from fastapi.testclient import TestClient

from prospector.aisa_client import AisaClient
from webapp.main import criar_app
from webapp.settings import WebConfigError, WebSettings

from tests.test_prospector import PROPOSTA_JSON, FalsaResposta, config_teste, item_maps


def settings_teste(**mudancas) -> WebSettings:
    base = dict(
        auth_mode="none", firebase_project_id="", firebase_api_key="", firebase_app_id="",
        firebase_auth_domain="", allowed_emails=frozenset(), max_categories=3, max_depth=50,
        max_proposals=3,
    )
    base.update(mudancas)
    return WebSettings(**base)


def settings_firebase(**mudancas) -> WebSettings:
    return settings_teste(
        auth_mode="firebase", firebase_project_id="meu-projeto", firebase_api_key="chave-publica",
        firebase_app_id="1:1:web:abc", firebase_auth_domain="meu-projeto.firebaseapp.com",
        allowed_emails=frozenset({"socio@exemplo.com"}), **mudancas,
    )


PEDIDO_BUSCA = {"category": "padaria", "min_rating": 4.5, "min_reviews": 10, "depth": 20, "demo": True}
OPCOES = {"price_total": 2500, "installments": 3, "delivery_days": 15, "validity_days": 7}


def app_demo(**kw):
    return criar_app(settings=kw.pop("settings", settings_teste()), cfg=kw.pop("cfg", config_teste(api_key="")), **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(app_demo())

    def buscar_demo(self) -> list[dict]:
        resposta = self.cliente.post("/api/search", json=PEDIDO_BUSCA)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        return resposta.json()["leads"]

    def aprovados(self) -> list[dict]:
        return [l for l in self.buscar_demo() if l["approved"]]

    def proposta_de(self, lead: dict) -> dict:
        r = self.cliente.post("/api/proposal", json={"lead": lead, "use_llm": False})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["proposal"]


class TestFluxoDemo(Base):
    def test_pagina_e_config_publica(self):
        self.assertIn("Prospector", self.cliente.get("/").text)
        self.assertEqual(self.cliente.get("/static/app.js").status_code, 200)
        c = self.cliente.get("/api/config").json()
        self.assertEqual(c["authMode"], "none")
        self.assertFalse(c["hasApiKey"])
        self.assertEqual(c["limits"], {"maxCategories": 3, "maxDepth": 50, "maxProposals": 3})
        self.assertNotIn("api_key", json.dumps(c).lower().replace("firebase", ""))

    def test_busca_demo_devolve_todos_os_leads_triados(self):
        leads = self.buscar_demo()
        self.assertEqual(len(leads), 6)
        aprovados = [l for l in leads if l["approved"]]
        self.assertEqual(sorted(l["name"] for l in aprovados), ["Barbearia Navalha Fina (DEMO)", "Padaria Pão Quente (DEMO)"])
        descartado = next(l for l in leads if l["reason"] == "possui_site")
        self.assertEqual(descartado["reason_text"], "já possui site próprio")
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{12}", l["id"]) for l in leads))

    def test_busca_demo_ignora_cidade_personalizada(self):
        # Os dados de demonstração são fixos em Bragança Paulista; trocar a cidade no modo demo
        # não pode fazer tudo ser descartado por "fora_da_cidade".
        r = self.cliente.post("/api/search", json={**PEDIDO_BUSCA, "city_name": "Atibaia", "location_coordinate": "-23.1,-46.5,13z"})
        self.assertEqual(r.status_code, 200, r.text)
        aprovados = [l for l in r.json()["leads"] if l["approved"]]
        self.assertEqual(len(aprovados), 2)

    def test_proposta_sem_llm(self):
        lead = self.aprovados()[0]
        p = self.proposta_de(lead)
        self.assertEqual(p["source"], "padrao")
        self.assertEqual([i["name"] for i in p["scope"]], ["Página Inicial", "Catálogo de Produtos", "Integração WhatsApp", "SEO Local"])
        self.assertIn(lead["name"], p["summary"])

    def test_proposta_so_para_lead_aprovado(self):
        descartado = next(l for l in self.buscar_demo() if not l["approved"])
        r = self.cliente.post("/api/proposal", json={"lead": descartado, "use_llm": False})
        self.assertEqual(r.status_code, 422)

    def test_pdf(self):
        lead = self.aprovados()[0]
        r = self.cliente.post("/api/pdf", json={"lead": lead, "proposal": self.proposta_de(lead), **OPCOES})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn('filename="proposta-', r.headers["content-disposition"])
        self.assertEqual(r.headers["content-type"], "application/pdf")

    def test_zip_com_nomes_repetidos(self):
        lead = self.aprovados()[0]
        copia = {**lead, "place_id": "outro-local-com-mesmo-nome"}
        proposta = self.proposta_de(lead)
        itens = [{"lead": lead, "proposal": proposta}, {"lead": copia, "proposal": proposta}]
        r = self.cliente.post("/api/zip", json={"items": itens, **OPCOES})
        self.assertEqual(r.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            nomes = z.namelist()
            self.assertEqual(len(set(nomes)), 2)  # não sobrescreve o arquivo do outro
            self.assertTrue(all(z.read(n).startswith(b"%PDF") for n in nomes))

    def test_csv_com_situacao_e_origem_do_texto(self):
        leads = self.buscar_demo()
        primeiro = next(l for l in leads if l["approved"])
        primeiro["proposal_source"] = "padrao"
        r = self.cliente.post("/api/csv", json={"leads": leads})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"\xef\xbb\xbf"))  # BOM para o Excel
        texto = r.content.decode("utf-8-sig")
        self.assertEqual(len(texto.strip().splitlines()), 1 + 6)
        self.assertIn("já possui site próprio", texto)
        self.assertIn("texto padrão", texto)

    def test_o_json_da_busca_pode_ser_devolvido_como_esta(self):
        """A página reenvia os leads exatamente como recebeu (com campos extras que o servidor ignora)."""
        lead = {**self.aprovados()[0], "proposalStatus": "pronta", "campo_inventado": 1}
        self.assertEqual(self.cliente.post("/api/proposal", json={"lead": lead, "use_llm": False}).status_code, 200)

    def test_cabecalhos_de_seguranca(self):
        r = self.cliente.get("/api/config")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["x-frame-options"], "DENY")

    def test_rotas_antigas_com_estado_nao_existem_mais(self):
        self.assertEqual(self.cliente.get("/api/jobs/x").status_code, 404)


class TestValidacao(Base):
    def test_busca(self):
        casos = [
            {"category": ""}, {"category": "   "}, {"category": "x" * 61}, {"depth": 0}, {"depth": 51},
            {"min_rating": 5.5}, {"min_rating": -1}, {"min_reviews": -1},
        ]
        for mudanca in casos:
            r = self.cliente.post("/api/search", json={**PEDIDO_BUSCA, **mudanca})
            self.assertEqual(r.status_code, 422, mudanca)

    def test_categoria_com_espacos_extras_e_normalizada(self):
        self.assertEqual(self.cliente.post("/api/search", json={**PEDIDO_BUSCA, "category": "  pet   shop "}).status_code, 200)

    def test_limites_do_lead(self):
        lead = self.aprovados()[0]
        ruins = [
            {"name": ""}, {"name": "x" * 201}, {"rating": 6}, {"reviews": -1}, {"place_id": ""},
            {"social_links": ["a"] * 11}, {"social_links": ["x" * 31]}, {"phone": "9" * 41}, {"address": "x" * 401},
        ]
        for mudanca in ruins:
            r = self.cliente.post("/api/proposal", json={"lead": {**lead, **mudanca}, "use_llm": False})
            self.assertEqual(r.status_code, 422, mudanca)

    def test_limites_da_proposta(self):
        lead = self.aprovados()[0]
        proposta = self.proposta_de(lead)
        ruins = [
            {"summary": ""}, {"strengths": []}, {"weaknesses": ["x" * 401]}, {"scope": []}, {"arguments": ["a"] * 7},
            {"source": "outra"}, {"closing": ""}, {"scope": [{"name": "n", "description": "d" * 401}]},
        ]
        for mudanca in ruins:
            r = self.cliente.post("/api/pdf", json={"lead": lead, "proposal": {**proposta, **mudanca}, **OPCOES})
            self.assertEqual(r.status_code, 422, mudanca)

    def test_opcoes_comerciais(self):
        lead = self.aprovados()[0]
        proposta = self.proposta_de(lead)
        for mudanca in ({"price_total": 0}, {"installments": 0}, {"installments": 25}, {"delivery_days": 0}, {"validity_days": 366}):
            r = self.cliente.post("/api/pdf", json={"lead": lead, "proposal": proposta, **{**OPCOES, **mudanca}})
            self.assertEqual(r.status_code, 422, mudanca)

    def test_zip_respeita_o_limite_de_propostas(self):
        lead = self.aprovados()[0]
        item = {"lead": lead, "proposal": self.proposta_de(lead)}
        self.assertEqual(self.cliente.post("/api/zip", json={"items": [item] * 3, **OPCOES}).status_code, 200)
        self.assertEqual(self.cliente.post("/api/zip", json={"items": [item] * 4, **OPCOES}).status_code, 422)
        self.assertEqual(self.cliente.post("/api/zip", json={"items": [], **OPCOES}).status_code, 422)

    def test_csv_vazio_e_recusado(self):
        self.assertEqual(self.cliente.post("/api/csv", json={"leads": []}).status_code, 422)

    def test_corpo_que_nao_e_json(self):
        r = self.cliente.post("/api/search", content="isto não é json", headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 422)


class TestBuscaReal(unittest.TestCase):
    """Fluxo com a AIsa simulada (sem modo demonstração)."""

    ENVELOPE = {"tasks": [{"status_code": 20000, "result": [{"items": [item_maps(), item_maps(place_id="p2", title="Com Site", domain="x.com.br")]}]}]}

    def cliente_com_aisa(self, cfg=None, post=None, **kw):
        chat = {"choices": [{"message": {"content": json.dumps(PROPOSTA_JSON)}}]}
        chamadas = []

        def padrao(url, json=None, timeout=None):
            chamadas.append((url, json))
            return FalsaResposta(200, self.ENVELOPE if "dataforseo" in url else chat)

        sessao = mock.MagicMock()
        sessao.headers = {}
        sessao.post = post or padrao
        app = criar_app(
            settings=settings_teste(),
            cfg=cfg or config_teste(api_key="chave-secreta-xyz"),
            fabrica_cliente=lambda c: AisaClient(c.api_key, c.base_url, session=sessao),
        )
        return TestClient(app, **kw), chamadas

    def busca_real(self, cliente):
        return cliente.post("/api/search", json={**PEDIDO_BUSCA, "demo": False})

    def test_busca_real_chama_o_maps_com_a_categoria_pedida(self):
        cliente, chamadas = self.cliente_com_aisa()
        r = self.busca_real(cliente)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([l["approved"] for l in r.json()["leads"]], [True, False])
        url, corpo = chamadas[0]
        self.assertTrue(url.endswith("/serp/google/maps/live/advanced"))
        self.assertEqual((corpo[0]["keyword"], corpo[0]["depth"]), ("padaria", 20))

    def test_filtros_do_pedido_valem_so_naquela_busca(self):
        cliente, _ = self.cliente_com_aisa()
        exigente = cliente.post("/api/search", json={**PEDIDO_BUSCA, "demo": False, "min_rating": 4.9}).json()["leads"]
        self.assertEqual([l["reason"] for l in exigente], ["nota_baixa", "possui_site"])
        normal = self.busca_real(cliente).json()["leads"]
        self.assertEqual([l["reason"] for l in normal], ["ok", "possui_site"])

    def test_cidade_e_localizacao_personalizadas_valem_so_naquela_busca(self):
        cliente, chamadas = self.cliente_com_aisa()
        r = cliente.post(
            "/api/search",
            json={**PEDIDO_BUSCA, "demo": False, "city_name": "Atibaia", "location_coordinate": "-23.1178,-46.5503,13z"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        # Os dois itens falsos têm endereço em Bragança Paulista: com a cidade trocada para
        # Atibaia, nenhum deles bate mais e os dois são descartados por "fora_da_cidade".
        self.assertEqual([l["reason"] for l in r.json()["leads"]], ["fora_da_cidade", "fora_da_cidade"])
        self.assertEqual(chamadas[0][1][0]["location_coordinate"], "-23.1178,-46.5503,13z")
        # Sem cidade/localização no pedido, volta a usar o padrão do servidor (Bragança Paulista).
        normal = self.busca_real(cliente).json()["leads"]
        self.assertEqual([l["reason"] for l in normal], ["ok", "possui_site"])

    def test_localizacao_em_formato_invalido_e_recusada(self):
        cliente, _ = self.cliente_com_aisa()
        r = cliente.post("/api/search", json={**PEDIDO_BUSCA, "demo": False, "location_coordinate": "não é uma coordenada"})
        self.assertEqual(r.status_code, 422)

    def test_proposta_com_llm(self):
        cliente, chamadas = self.cliente_com_aisa()
        lead = self.busca_real(cliente).json()["leads"][0]
        r = cliente.post("/api/proposal", json={"lead": lead, "use_llm": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["proposal"]["source"], "llm")
        self.assertTrue(chamadas[-1][0].endswith("/v1/chat/completions"))
        self.assertNotIn("chave-secreta-xyz", r.text)

    def test_sem_chave_a_busca_real_e_o_llm_sao_recusados_mas_a_demo_funciona(self):
        cliente = TestClient(app_demo())
        r = cliente.post("/api/search", json={**PEDIDO_BUSCA, "demo": False})
        self.assertEqual(r.status_code, 400)
        self.assertIn("AISA_API_KEY", r.json()["detail"])
        lead = cliente.post("/api/search", json=PEDIDO_BUSCA).json()["leads"][0]
        self.assertEqual(cliente.post("/api/proposal", json={"lead": lead, "use_llm": True}).status_code, 400)
        self.assertEqual(cliente.post("/api/proposal", json={"lead": lead, "use_llm": False}).status_code, 200)

    def test_erro_de_conta_da_aisa_vira_424_e_nunca_401(self):
        """401 da AIsa não pode voltar como 401: a página entenderia como 'sessão expirada'."""
        for status, trecho in [(401, "Chave da AIsa"), (402, "saldo"), (403, "permissão"), (404, "Rota")]:
            cliente, _ = self.cliente_com_aisa(post=lambda url, json=None, timeout=None, s=status: FalsaResposta(s, texto="x"))
            r = self.busca_real(cliente)
            self.assertEqual(r.status_code, 424, status)
            self.assertIn(trecho, r.json()["detail"])

    def test_erro_de_conta_no_llm_tambem_vira_424(self):
        def post(url, json=None, timeout=None):
            return FalsaResposta(200, self.ENVELOPE) if "dataforseo" in url else FalsaResposta(402, texto="sem saldo")

        cliente, _ = self.cliente_com_aisa(post=post)
        lead = self.busca_real(cliente).json()["leads"][0]
        r = cliente.post("/api/proposal", json={"lead": lead, "use_llm": True})
        self.assertEqual(r.status_code, 424)
        self.assertIn("saldo", r.json()["detail"])

    def test_instabilidade_da_aisa_vira_502_na_busca_e_texto_padrao_na_proposta(self):
        cliente, _ = self.cliente_com_aisa(post=lambda url, json=None, timeout=None: FalsaResposta(503, texto="fora do ar"))
        self.assertEqual(self.busca_real(cliente).status_code, 502)

        def post(url, json=None, timeout=None):
            return FalsaResposta(200, self.ENVELOPE) if "dataforseo" in url else FalsaResposta(503, texto="fora do ar")

        cliente, _ = self.cliente_com_aisa(post=post)
        lead = self.busca_real(cliente).json()["leads"][0]
        r = cliente.post("/api/proposal", json={"lead": lead, "use_llm": True})
        self.assertEqual((r.status_code, r.json()["proposal"]["source"]), (200, "padrao"))  # o lead não se perde

    def test_llm_com_resposta_fora_do_formato_usa_texto_padrao(self):
        def post(url, json=None, timeout=None):
            if "dataforseo" in url:
                return FalsaResposta(200, self.ENVELOPE)
            return FalsaResposta(200, {"choices": [{"message": {"content": "não sou um JSON"}}]})

        cliente, _ = self.cliente_com_aisa(post=post)
        lead = self.busca_real(cliente).json()["leads"][0]
        r = cliente.post("/api/proposal", json={"lead": lead, "use_llm": True})
        self.assertEqual(r.json()["proposal"]["source"], "padrao")

    def test_excecao_inesperada_devolve_500_sem_vazar_detalhes(self):
        def post(url, json=None, timeout=None):
            raise RuntimeError("segredo-interno-123")

        cliente, _ = self.cliente_com_aisa(post=post, raise_server_exceptions=False)
        r = self.busca_real(cliente)
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("segredo-interno-123", r.text)


class TestAutenticacao(unittest.TestCase):
    def cliente(self, emails_por_token, settings=None, **kw):
        def verificar(token):
            if token not in emails_por_token:
                raise ValueError("token inválido")
            return emails_por_token[token]

        app = criar_app(settings=settings or settings_firebase(), cfg=config_teste(api_key=""), verificar_token=verificar, **kw)
        return TestClient(app)

    def cab(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_config_e_pagina_sao_publicas_mas_expoem_so_dados_publicos_do_firebase(self):
        c = self.cliente({})
        dados = c.get("/api/config").json()
        self.assertEqual(dados["authMode"], "firebase")
        self.assertEqual(set(dados["firebase"]), {"apiKey", "authDomain", "projectId", "appId"})
        self.assertEqual(c.get("/").status_code, 200)

    def test_toda_rota_da_api_exige_token(self):
        c = self.cliente({"tok-socio-0000000000000": "socio@exemplo.com"})
        rotas = [
            ("/api/search", PEDIDO_BUSCA),
            ("/api/proposal", {"lead": {"place_id": "p", "name": "n"}, "use_llm": False}),
            ("/api/pdf", {}), ("/api/zip", {}), ("/api/csv", {}),
        ]
        for caminho, corpo in rotas:
            self.assertEqual(c.post(caminho, json=corpo).status_code, 401, caminho)  # sem token
            self.assertEqual(c.post(caminho, json=corpo, headers={"Authorization": "Basic abc"}).status_code, 401, caminho)
            self.assertEqual(c.post(caminho, json=corpo, headers=self.cab("token-falso-000000000000")).status_code, 401, caminho)

    def test_token_valido_e_aceito(self):
        c = self.cliente({"tok-socio-0000000000000": "socio@exemplo.com"})
        r = c.post("/api/search", json=PEDIDO_BUSCA, headers=self.cab("tok-socio-0000000000000"))
        self.assertEqual(r.status_code, 200)

    def test_validacao_so_acontece_depois_da_autenticacao(self):
        """Sem token, nem a estrutura do pedido é revelada (401, não 422)."""
        c = self.cliente({})
        self.assertEqual(c.post("/api/search", json={}).status_code, 401)

    def test_email_fora_da_lista_recebe_403(self):
        c = self.cliente({"tok-intruso-000000000000": "intruso@exemplo.com"})
        self.assertEqual(c.post("/api/search", json=PEDIDO_BUSCA, headers=self.cab("tok-intruso-000000000000")).status_code, 403)

    def test_email_sem_valor_e_recusado(self):
        c = self.cliente({"tok-sem-email-00000000": ""})
        self.assertEqual(c.post("/api/search", json=PEDIDO_BUSCA, headers=self.cab("tok-sem-email-00000000")).status_code, 403)

    def test_verificador_do_firebase_e_chamado_com_o_projeto_certo(self):
        with mock.patch("google.oauth2.id_token.verify_firebase_token", return_value={"email": "Socio@Exemplo.com"}) as verificar:
            c = TestClient(criar_app(settings=settings_firebase(), cfg=config_teste(api_key="")))
            r = c.post("/api/search", json=PEDIDO_BUSCA, headers=self.cab("um-token-qualquer-000000000"))
        self.assertEqual(r.status_code, 200)  # e-mail normalizado para minúsculas e aceito
        self.assertEqual(verificar.call_args.kwargs["audience"], "meu-projeto")


class TestSettings(unittest.TestCase):
    def ambiente(self, **variaveis):
        return mock.patch.dict(os.environ, variaveis, clear=True)

    def test_firebase_sem_lista_de_emails_nao_sobe(self):
        completo = dict(FIREBASE_PROJECT_ID="p", FIREBASE_WEB_API_KEY="k", FIREBASE_APP_ID="a")
        with self.ambiente(**completo), mock.patch("webapp.settings.load_dotenv"):
            with self.assertRaisesRegex(WebConfigError, "ALLOWED_EMAILS"):
                WebSettings.from_env()

    def test_firebase_sem_chaves_publicas_nao_sobe(self):
        with self.ambiente(ALLOWED_EMAILS="a@x.com"), mock.patch("webapp.settings.load_dotenv"):
            with self.assertRaisesRegex(WebConfigError, "FIREBASE_PROJECT_ID"):
                WebSettings.from_env()

    def test_texto_de_exemplo_do_env_example_conta_como_nao_preenchido(self):
        env = dict(FIREBASE_PROJECT_ID="proj", FIREBASE_WEB_API_KEY="cole-o-apiKey-do-firebase-config",
                   FIREBASE_APP_ID="cole-o-appId-do-firebase-config", ALLOWED_EMAILS="a@x.com")
        with self.ambiente(**env), mock.patch("webapp.settings.load_dotenv"):
            with self.assertRaisesRegex(WebConfigError, "FIREBASE_WEB_API_KEY, FIREBASE_APP_ID"):
                WebSettings.from_env()

    def test_modo_none_nao_exige_nada(self):
        with self.ambiente(AUTH_MODE="none"), mock.patch("webapp.settings.load_dotenv"):
            s = WebSettings.from_env()
            self.assertEqual((s.auth_mode, s.max_depth, s.max_proposals), ("none", 100, 15))
            self.assertIsNone(s.firebase_public())

    def test_valores_invalidos(self):
        with self.ambiente(AUTH_MODE="qualquer"), mock.patch("webapp.settings.load_dotenv"):
            with self.assertRaises(WebConfigError):
                WebSettings.from_env()
        with self.ambiente(AUTH_MODE="none", MAX_DEPTH="9999"), mock.patch("webapp.settings.load_dotenv"):
            with self.assertRaisesRegex(WebConfigError, "MAX_DEPTH"):
                WebSettings.from_env()

    def test_configuracao_completa_le_emails(self):
        env = dict(FIREBASE_PROJECT_ID="proj", FIREBASE_WEB_API_KEY="k", FIREBASE_APP_ID="a", ALLOWED_EMAILS=" A@x.com , b@x.com ,")
        with self.ambiente(**env), mock.patch("webapp.settings.load_dotenv"):
            s = WebSettings.from_env()
        self.assertEqual(s.allowed_emails, frozenset({"a@x.com", "b@x.com"}))
        self.assertEqual(s.firebase_auth_domain, "proj.firebaseapp.com")


class TestEntradaDaVercel(unittest.TestCase):
    """A Vercel procura um FastAPI chamado `app` no arquivo app.py da raiz."""

    def importar(self, **variaveis):
        sys.modules.pop("app", None)
        with mock.patch.dict(os.environ, variaveis, clear=True), \
                mock.patch("webapp.settings.load_dotenv"), mock.patch("prospector.config.load_dotenv"):
            return importlib.import_module("app")

    def tearDown(self):
        sys.modules.pop("app", None)

    def test_expoe_app_pronto_para_servir(self):
        modulo = self.importar(AUTH_MODE="none")
        cliente = TestClient(modulo.app)
        self.assertEqual(cliente.get("/api/config").status_code, 200)
        self.assertEqual(cliente.post("/api/search", json=PEDIDO_BUSCA).status_code, 200)

    def test_configuracao_insegura_mostra_mensagem_clara_em_vez_de_500_genérico(self):
        modulo = self.importar()  # modo firebase (padrão) sem nenhuma variável
        cliente = TestClient(modulo.app, raise_server_exceptions=False)
        resposta = cliente.get("/")
        self.assertEqual(resposta.status_code, 500)
        self.assertIn("Faltam no .env", resposta.text)
        # nenhuma rota real fica exposta: qualquer caminho cai na mesma mensagem de erro
        self.assertIn("Faltam no .env", cliente.post("/api/search", json={}).text)


if __name__ == "__main__":
    unittest.main()
