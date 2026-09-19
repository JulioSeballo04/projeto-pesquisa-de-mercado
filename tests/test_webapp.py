"""Testes do app web (FastAPI). Sem internet, sem chave e sem Firebase de verdade."""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from prospector.aisa_client import AisaClient, AisaError
from prospector.config import Config
from webapp.main import criar_app
from webapp.settings import WebConfigError, WebSettings

from tests.test_prospector import PROPOSTA_JSON, FalsaResposta, config_teste, item_maps


def settings_teste(**mudancas) -> WebSettings:
    base = dict(
        auth_mode="none", firebase_project_id="", firebase_api_key="", firebase_app_id="",
        firebase_auth_domain="", allowed_emails=frozenset(), max_categories=3, max_depth=50,
        max_proposals=3, job_ttl_seconds=3600,
    )
    base.update(mudancas)
    return WebSettings(**base)


def settings_firebase(**mudancas) -> WebSettings:
    return settings_teste(
        auth_mode="firebase", firebase_project_id="meu-projeto", firebase_api_key="chave-publica",
        firebase_app_id="1:1:web:abc", firebase_auth_domain="meu-projeto.firebaseapp.com",
        allowed_emails=frozenset({"socio@exemplo.com"}), **mudancas,
    )


def esperar(cliente: TestClient, job_id: str, cabecalhos=None, limite=10.0) -> dict:
    """Consulta a tarefa até ela sair de 'running' (as tarefas rodam em threads)."""
    fim = time.time() + limite
    while time.time() < fim:
        dados = cliente.get(f"/api/jobs/{job_id}", headers=cabecalhos or {}).json()
        if dados["status"] != "running":
            return dados
        time.sleep(0.02)
    raise AssertionError("a tarefa não terminou a tempo")


PEDIDO_BUSCA = {"categories": ["padaria"], "min_rating": 4.5, "min_reviews": 10, "depth": 20, "demo": True}
PEDIDO_PROPOSTAS = {"price_total": 2500, "installments": 3, "delivery_days": 15, "validity_days": 7, "use_llm": False}


def app_demo(**kw):
    return criar_app(settings=kw.pop("settings", settings_teste()), cfg=kw.pop("cfg", config_teste(api_key="")), **kw)


class TestFluxoDemo(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(app_demo())

    def buscar(self) -> dict:
        resposta = self.cliente.post("/api/search", json=PEDIDO_BUSCA)
        self.assertEqual(resposta.status_code, 202, resposta.text)
        return esperar(self.cliente, resposta.json()["jobId"])

    def test_pagina_e_config_publica(self):
        self.assertIn("Prospector", self.cliente.get("/").text)
        self.assertEqual(self.cliente.get("/static/app.js").status_code, 200)
        c = self.cliente.get("/api/config").json()
        self.assertEqual(c["authMode"], "none")
        self.assertFalse(c["hasApiKey"])
        self.assertEqual(c["limits"], {"maxCategories": 3, "maxDepth": 50, "maxProposals": 3})
        self.assertNotIn("api_key", json.dumps(c).lower().replace("firebase", ""))

    def test_busca_demo_triagem(self):
        job = self.buscar()
        self.assertEqual(job["status"], "done")
        aprovados = [l for l in job["leads"] if l["approved"]]
        self.assertEqual(len(job["leads"]), 6)
        self.assertEqual(sorted(l["name"] for l in aprovados), ["Barbearia Navalha Fina (DEMO)", "Padaria Pão Quente (DEMO)"])
        descartado = next(l for l in job["leads"] if l["reason"] == "possui_site")
        self.assertEqual(descartado["reasonText"], "já possui site próprio")
        self.assertNotIn("password", json.dumps(job))

    def test_gerar_pdf_zip_e_csv(self):
        job = self.buscar()
        ids = [l["id"] for l in job["leads"] if l["approved"]]
        r = self.cliente.post(f"/api/jobs/{job['id']}/proposals", json={**PEDIDO_PROPOSTAS, "lead_ids": ids})
        self.assertEqual(r.status_code, 202, r.text)
        depois = esperar(self.cliente, job["id"])
        self.assertEqual([l["proposalStatus"] for l in depois["leads"] if l["approved"]], ["pronta", "pronta"])

        pdf = self.cliente.get(f"/api/jobs/{job['id']}/leads/{ids[0]}/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn("attachment", pdf.headers["content-disposition"])

        zip_ = self.cliente.get(f"/api/jobs/{job['id']}/proposals.zip")
        with zipfile.ZipFile(io.BytesIO(zip_.content)) as z:
            self.assertEqual(len(z.namelist()), 2)
            self.assertTrue(all(n.endswith(".pdf") for n in z.namelist()))

        csv = self.cliente.get(f"/api/jobs/{job['id']}/leads.csv")
        self.assertTrue(csv.content.startswith(b"\xef\xbb\xbf"))  # BOM para o Excel
        texto = csv.content.decode("utf-8-sig")
        self.assertIn("já possui site próprio", texto)
        self.assertIn("texto padrão", texto)

    def test_preco_da_proposta_vai_para_o_pdf(self):
        job = self.buscar()
        ids = [l["id"] for l in job["leads"] if l["approved"]][:1]
        self.cliente.post(f"/api/jobs/{job['id']}/proposals", json={**PEDIDO_PROPOSTAS, "lead_ids": ids, "price_total": 4000, "installments": 4})
        esperar(self.cliente, job["id"])
        pdf = self.cliente.get(f"/api/jobs/{job['id']}/leads/{ids[0]}/pdf").content
        self.assertGreater(len(pdf), 2000)  # gerado com a config da proposta, sem quebrar

    def test_so_leads_aprovados_geram_proposta(self):
        job = self.buscar()
        descartado = next(l["id"] for l in job["leads"] if not l["approved"])
        r = self.cliente.post(f"/api/jobs/{job['id']}/proposals", json={**PEDIDO_PROPOSTAS, "lead_ids": [descartado]})
        self.assertEqual(r.status_code, 422)
        r = self.cliente.post(f"/api/jobs/{job['id']}/proposals", json={**PEDIDO_PROPOSTAS, "lead_ids": ["inexistente"]})
        self.assertEqual(r.status_code, 422)

    def test_pdf_antes_de_gerar_da_404(self):
        job = self.buscar()
        lead = next(l["id"] for l in job["leads"] if l["approved"])
        self.assertEqual(self.cliente.get(f"/api/jobs/{job['id']}/leads/{lead}/pdf").status_code, 404)
        self.assertEqual(self.cliente.get(f"/api/jobs/{job['id']}/proposals.zip").status_code, 404)

    def test_tarefa_inexistente(self):
        self.assertEqual(self.cliente.get("/api/jobs/naoexiste").status_code, 404)

    def test_cabecalhos_de_seguranca(self):
        r = self.cliente.get("/api/config")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["x-frame-options"], "DENY")


class TestLimitesDeCusto(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(app_demo())

    def test_validacao_da_busca(self):
        casos = [
            ({**PEDIDO_BUSCA, "categories": []}, 422),
            ({**PEDIDO_BUSCA, "categories": ["a", "b", "c", "d"]}, 422),  # limite do servidor = 3
            ({**PEDIDO_BUSCA, "depth": 51}, 422),  # limite do servidor = 50
            ({**PEDIDO_BUSCA, "depth": 0}, 422),
            ({**PEDIDO_BUSCA, "min_rating": 5.5}, 422),
            ({**PEDIDO_BUSCA, "categories": ["x" * 61]}, 422),
            ({**PEDIDO_BUSCA, "categories": ["  "]}, 422),
            ({**PEDIDO_BUSCA, "min_reviews": -1}, 422),
        ]
        for corpo, esperado in casos:
            self.assertEqual(self.cliente.post("/api/search", json=corpo).status_code, esperado, corpo)

    def test_categorias_repetidas_sao_unificadas(self):
        r = self.cliente.post("/api/search", json={**PEDIDO_BUSCA, "categories": ["Padaria", "padaria", " padaria "]})
        self.assertEqual(r.status_code, 202)

    def test_limite_de_propostas_por_vez(self):
        job = esperar(self.cliente, self.cliente.post("/api/search", json=PEDIDO_BUSCA).json()["jobId"])
        ids = [l["id"] for l in job["leads"]]
        r = self.cliente.post(f"/api/jobs/{job['id']}/proposals", json={**PEDIDO_PROPOSTAS, "lead_ids": ids[:4]})
        self.assertEqual(r.status_code, 422)  # 4 > max_proposals (3)

    def test_validacao_da_proposta(self):
        job = esperar(self.cliente, self.cliente.post("/api/search", json=PEDIDO_BUSCA).json()["jobId"])
        ids = [l["id"] for l in job["leads"] if l["approved"]]
        for mudanca in ({"price_total": 0}, {"installments": 0}, {"installments": 25}, {"delivery_days": 0}, {"lead_ids": []}):
            corpo = {**PEDIDO_PROPOSTAS, "lead_ids": ids, **mudanca}
            self.assertEqual(self.cliente.post(f"/api/jobs/{job['id']}/proposals", json=corpo).status_code, 422, mudanca)

    def test_uma_tarefa_por_vez(self):
        """Dois pedidos seguidos: o segundo é recusado enquanto o primeiro roda (evita cobrança dobrada)."""
        with mock.patch("webapp.main.executar_busca", lambda *a, **k: time.sleep(0.5)):
            r1 = self.cliente.post("/api/search", json=PEDIDO_BUSCA)
            r2 = self.cliente.post("/api/search", json=PEDIDO_BUSCA)
        self.assertEqual((r1.status_code, r2.status_code), (202, 409))


class TestBuscaReal(unittest.TestCase):
    """Fluxo com a AIsa simulada (sem modo demonstração)."""

    def cliente_com_aisa(self, cfg=None, post=None):
        envelope = {"tasks": [{"status_code": 20000, "result": [{"items": [item_maps(), item_maps(place_id="p2", title="Com Site", domain="x.com.br")]}]}]}
        chat = {"choices": [{"message": {"content": json.dumps(PROPOSTA_JSON)}}]}
        chamadas = []

        def padrao(url, json=None, timeout=None):
            chamadas.append(url)
            return FalsaResposta(200, envelope if "dataforseo" in url else chat)

        sessao = mock.MagicMock()
        sessao.headers = {}
        sessao.post = post or padrao
        app = criar_app(
            settings=settings_teste(),
            cfg=cfg or config_teste(api_key="chave-secreta-xyz"),
            fabrica_cliente=lambda c: AisaClient(c.api_key, c.base_url, session=sessao),
        )
        return TestClient(app), chamadas

    def test_busca_real_e_proposta_com_llm(self):
        cliente, chamadas = self.cliente_com_aisa()
        body = {**PEDIDO_BUSCA, "demo": False}
        job = esperar(cliente, cliente.post("/api/search", json=body).json()["jobId"])
        self.assertEqual(job["status"], "done", job)
        self.assertEqual([l["approved"] for l in job["leads"]], [True, False])
        ids = [l["id"] for l in job["leads"] if l["approved"]]
        cliente.post(f"/api/jobs/{job['id']}/proposals", json={**PEDIDO_PROPOSTAS, "lead_ids": ids, "use_llm": True})
        depois = esperar(cliente, job["id"])
        self.assertEqual(depois["leads"][0]["proposalSource"], "llm")
        self.assertEqual([u.rsplit("/", 1)[-1] for u in chamadas], ["advanced", "completions"])
        self.assertNotIn("chave-secreta-xyz", json.dumps(depois))

    def test_sem_chave_a_busca_real_e_recusada_mas_a_demo_funciona(self):
        cliente = TestClient(app_demo())
        r = cliente.post("/api/search", json={**PEDIDO_BUSCA, "demo": False})
        self.assertEqual(r.status_code, 400)
        self.assertIn("AISA_API_KEY", r.json()["detail"])
        self.assertEqual(cliente.post("/api/search", json=PEDIDO_BUSCA).status_code, 202)

    def test_erro_da_aisa_vira_mensagem_na_tarefa(self):
        cliente, _ = self.cliente_com_aisa(post=lambda url, json=None, timeout=None: FalsaResposta(401, texto="unauthorized"))
        job = esperar(cliente, cliente.post("/api/search", json={**PEDIDO_BUSCA, "demo": False}).json()["jobId"])
        self.assertEqual(job["status"], "error")
        self.assertIn("Chave da AIsa", job["error"])

    def test_erro_de_conta_no_llm_interrompe_as_propostas_seguintes(self):
        def post(url, json=None, timeout=None):
            if "dataforseo" in url:
                itens = [item_maps(place_id=f"p{i}", title=f"Padaria {i}") for i in range(3)]
                return FalsaResposta(200, {"tasks": [{"status_code": 20000, "result": [{"items": itens}]}]})
            return FalsaResposta(402, texto="sem saldo")

        cliente, _ = self.cliente_com_aisa(post=post)
        job = esperar(cliente, cliente.post("/api/search", json={**PEDIDO_BUSCA, "demo": False}).json()["jobId"])
        ids = [l["id"] for l in job["leads"]]
        cliente.post(f"/api/jobs/{job['id']}/proposals", json={**PEDIDO_PROPOSTAS, "lead_ids": ids, "use_llm": True})
        depois = esperar(cliente, job["id"])
        self.assertEqual([l["proposalStatus"] for l in depois["leads"]], ["erro", "erro", "erro"])
        self.assertIn("saldo", depois["error"])

    def test_excecao_inesperada_nao_deixa_a_tarefa_presa(self):
        def post(url, json=None, timeout=None):
            raise RuntimeError("bug")

        cliente, _ = self.cliente_com_aisa(post=post)
        job = esperar(cliente, cliente.post("/api/search", json={**PEDIDO_BUSCA, "demo": False}).json()["jobId"])
        self.assertEqual(job["status"], "error")


class TestAutenticacao(unittest.TestCase):
    def cliente(self, emails_por_token, **kw):
        def verificar(token):
            if token not in emails_por_token:
                raise ValueError("token inválido")
            return emails_por_token[token]

        app = criar_app(settings=settings_firebase(), cfg=config_teste(api_key=""), verificar_token=verificar, **kw)
        return TestClient(app)

    def cab(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_config_e_pagina_sao_publicas_mas_expoem_so_dados_publicos_do_firebase(self):
        c = self.cliente({})
        dados = c.get("/api/config").json()
        self.assertEqual(dados["authMode"], "firebase")
        self.assertEqual(set(dados["firebase"]), {"apiKey", "authDomain", "projectId", "appId"})
        self.assertEqual(c.get("/").status_code, 200)

    def test_api_exige_token_valido(self):
        c = self.cliente({"tok-socio-0000000000000": "socio@exemplo.com"})
        self.assertEqual(c.post("/api/search", json=PEDIDO_BUSCA).status_code, 401)  # sem token
        self.assertEqual(c.post("/api/search", json=PEDIDO_BUSCA, headers={"Authorization": "Basic abc"}).status_code, 401)
        self.assertEqual(c.post("/api/search", json=PEDIDO_BUSCA, headers=self.cab("token-falso-000000000000")).status_code, 401)
        self.assertEqual(c.get("/api/jobs/x").status_code, 401)
        self.assertEqual(c.get("/api/jobs/x/leads.csv").status_code, 401)
        r = c.post("/api/search", json=PEDIDO_BUSCA, headers=self.cab("tok-socio-0000000000000"))
        self.assertEqual(r.status_code, 202)

    def test_email_fora_da_lista_recebe_403(self):
        c = self.cliente({"tok-intruso-000000000000": "intruso@exemplo.com"})
        r = c.post("/api/search", json=PEDIDO_BUSCA, headers=self.cab("tok-intruso-000000000000"))
        self.assertEqual(r.status_code, 403)

    def test_email_sem_valor_e_recusado(self):
        c = self.cliente({"tok-sem-email-00000000": ""})
        self.assertEqual(c.get("/api/jobs/x", headers=self.cab("tok-sem-email-00000000")).status_code, 403)

    def test_comparacao_de_email_ignora_maiusculas(self):
        c = self.cliente({"tok-maiusculo-0000000000": "SOCIO@Exemplo.com".lower()})
        self.assertEqual(c.get("/api/jobs/x", headers=self.cab("tok-maiusculo-0000000000")).status_code, 404)  # autenticou; a tarefa não existe

    def test_cada_pessoa_so_ve_as_proprias_tarefas(self):
        settings = settings_firebase(); settings = WebSettings(**{**settings.__dict__, "allowed_emails": frozenset({"a@x.com", "b@x.com"})})
        tokens = {"tok-a-000000000000000000": "a@x.com", "tok-b-000000000000000000": "b@x.com"}
        c = TestClient(criar_app(settings=settings, cfg=config_teste(api_key=""), verificar_token=lambda t: tokens[t]))
        job_id = c.post("/api/search", json=PEDIDO_BUSCA, headers=self.cab("tok-a-000000000000000000")).json()["jobId"]
        esperar(c, job_id, self.cab("tok-a-000000000000000000"))
        for caminho in (f"/api/jobs/{job_id}", f"/api/jobs/{job_id}/leads.csv"):
            self.assertEqual(c.get(caminho, headers=self.cab("tok-b-000000000000000000")).status_code, 404)
            self.assertEqual(c.get(caminho, headers=self.cab("tok-a-000000000000000000")).status_code, 200)

    def test_verificador_do_firebase_e_chamado_com_o_projeto_certo(self):
        with mock.patch("google.oauth2.id_token.verify_firebase_token", return_value={"email": "Socio@Exemplo.com"}) as verificar:
            app = criar_app(settings=settings_firebase(), cfg=config_teste(api_key=""))
            c = TestClient(app)
            r = c.get("/api/jobs/x", headers=self.cab("um-token-qualquer-000000000"))
        self.assertEqual(r.status_code, 404)  # autenticado (e-mail normalizado); tarefa inexistente
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


if __name__ == "__main__":
    unittest.main()
