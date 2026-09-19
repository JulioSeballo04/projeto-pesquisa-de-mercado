# Prospector — propostas para negócios de Bragança Paulista sem site

Existe em duas formas, com o mesmo núcleo: **linha de comando** (este README, a partir do passo a passo) e **app web** para usar do celular, tablet ou qualquer computador (seção "App web" logo abaixo).

O agente faz, sozinho, este fluxo:

1. **Pesquisa** no Google Maps (via AIsa) negócios de uma categoria em Bragança Paulista/SP.
2. **Filtra**: só ficam os bem avaliados (nota mínima e número mínimo de avaliações), com telefone e **sem site próprio**. Quem cadastrou só Instagram, Facebook, WhatsApp, Linktree ou iFood no lugar do site continua sendo alvo.
3. **Escreve** uma proposta de site profissional com IA (diagnóstico, escopo, investimento, prazo e argumentos).
4. **Gera um PDF** por empresa e uma **planilha** (`leads.csv`) com todos os estabelecimentos triados e o motivo de cada descarte.

O programa **não envia nada** para ninguém: ele só produz os arquivos. Revise cada PDF e faça o contato você mesmo.

## Importante: o que a AIsa oferece hoje

O catálogo público da AIsa lista "Google Places" e "Google Maps" como *Coming Soon*. O Google Maps já está disponível pelo **DataForSEO**, que a AIsa expõe com a mesma chave (`POST /apis/v1/dataforseo/serp/google/maps/live/advanced`), e é essa rota que o programa usa. O LLM usa `POST /v1/chat/completions` (formato OpenAI). Uma única chave (`Authorization: Bearer`) cobre as duas coisas.

Fontes: [catálogo](https://aisa.one/api) · [Google Maps SERP](https://aisa.one/docs/api-reference/dataforseo/post_dataforseo-serp-google-maps-live-advanced) · [Chat API](https://aisa.one/docs/api-reference/chat/post_chat-completions).

> O programa foi testado com respostas simuladas (76 testes automáticos). **Ainda não foi executado com uma chave real**, então na primeira execução use `--dry-run` (passo 5) para conferir se o formato dos dados bate com o esperado.

## App web (usar em qualquer aparelho)

A mesma busca, triagem, proposta e PDF, numa página que abre no navegador do celular, tablet ou computador: você escolhe as categorias, vê os leads (aprovados e descartados com o motivo), marca os que quer, gera as propostas e baixa PDF, ZIP ou planilha.

**Como é protegido.** A chave da AIsa fica **só no servidor**; a página nunca a recebe. O acesso exige login com o mesmo Firebase do app de pesquisa e só entra quem estiver na lista `ALLOWED_EMAILS`. Sem essa lista o servidor nem sobe. Os limites de custo (`MAX_CATEGORIES`, `MAX_DEPTH`, `MAX_PROPOSALS`) valem no servidor, mesmo que alguém altere a página.

### Testar no seu computador (sem login, sem chave, sem custo)

```powershell
cd C:\projetos\prospector-agente
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:AUTH_MODE = "none"
python -m webapp
```

Abra http://127.0.0.1:8000. Sem chave da AIsa, a página entra em **modo demonstração** (empresas fictícias). O modo `AUTH_MODE=none` só aceita conexões do próprio computador; o servidor recusa subir com ele em outro endereço.

### Ligar o login e usar de verdade

1. No `.env`, preencha `AISA_API_KEY` (como no passo 3 abaixo).
2. Copie do `firebase-config.js` do app de pesquisa: `projectId` para `FIREBASE_PROJECT_ID`, `apiKey` para `FIREBASE_WEB_API_KEY` e `appId` para `FIREBASE_APP_ID`. (Não são segredos.)
3. Em `ALLOWED_EMAILS`, coloque os e-mails dos sócios (os mesmos cadastrados em Authentication do Firebase), separados por vírgula.
4. Deixe `AUTH_MODE=firebase` e rode `python -m webapp`.

### Publicar na internet

O app precisa de um servidor Python rodando (o GitHub Pages **não serve**, porque hospeda só páginas estáticas e não pode guardar a chave em segredo). Qualquer hospedagem que rode Docker ou Python 3.10+ serve (Render, Railway, Fly.io, um VPS...). Confira preço e limites do plano na própria hospedagem antes de escolher.

1. Suba esta pasta para um repositório **privado** (o `.gitignore` já impede o envio do `.env`).
2. Na hospedagem, crie um serviço a partir do repositório usando o `Dockerfile` (ou, sem Docker: instalar com `pip install -r requirements.txt` e iniciar com `python -m webapp`).
3. Cadastre no painel da hospedagem, como **variáveis de ambiente**, os mesmos valores do `.env`: `AISA_API_KEY`, `AISA_MODEL`, `COMPANY_NAME`, `COMPANY_CONTACT`, `FIREBASE_*`, `ALLOWED_EMAILS` e os preços. Defina também `HOST=0.0.0.0` (a plataforma normalmente já define a `PORT`).
4. No Firebase Console > Authentication > Configurações > **Domínios autorizados**, adicione o endereço que a hospedagem gerar.
5. Abra o endereço (é HTTPS). No celular, use "Adicionar à tela inicial" para virar um atalho.

Cuidados:

- **Um único processo.** As tarefas em andamento ficam em memória; se o servidor reiniciar, é só buscar de novo. Não configure vários "workers".
- Planos gratuitos costumam "dormir" quando ficam sem uso, e a primeira abertura demora um pouco.
- Nada fica gravado no servidor: os PDFs são montados na hora do download e as tarefas expiram após `JOB_TTL_SECONDS` (2 h por padrão).
- Cada pessoa só vê as próprias buscas, e só há uma tarefa por vez por pessoa (evita clique duplo cobrando em dobro).

## Passo a passo (Windows)

### 1. Instalar o Python
Precisa do Python 3.10 ou mais novo (https://www.python.org/downloads/). Marque **Add python.exe to PATH** na instalação. Confira no PowerShell:

```powershell
python --version
```

### 2. Preparar o ambiente
Na pasta do projeto (`C:\projetos\prospector-agente`):

```powershell
cd C:\projetos\prospector-agente
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Se o PowerShell reclamar de "execução de scripts desabilitada", rode uma vez `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` e repita a ativação.

### 3. Criar a chave da AIsa e configurar o `.env`
1. Entre em https://aisa.one, crie sua conta e, no console, crie/copie uma **chave de API** (formato `sk-aisa-...`). Confira se há saldo/crédito na conta.
2. Copie o arquivo de exemplo:
   ```powershell
   copy .env.example .env
   ```
3. Abra o `.env` no Bloco de Notas e preencha, no mínimo:
   - `AISA_API_KEY` — sua chave.
   - `COMPANY_NAME` e `COMPANY_CONTACT` — os dados da **sua** empresa que aparecem no PDF.
   - Se quiser, `PRICE_TOTAL`, `INSTALLMENTS`, `DELIVERY_DAYS` (preço e prazo da proposta).

**Segurança da chave:** ela só existe no `.env`. Esse arquivo já está no `.gitignore`, então não vai para o GitHub. Não cole a chave em conversas, e-mails ou prints. Se ela vazar, apague-a no console da AIsa e crie outra.

### 4. Testar sem gastar nada (modo demonstração)
Usa empresas **fictícias** e o texto padrão (sem IA), então não precisa de chave:

```powershell
python main.py --demo --sem-llm
```

Abra o PDF gerado em `output\<data-hora>\propostas\` para ver o layout, e o `leads.csv` para ver as regras de descarte funcionando.

### 5. Primeira busca real, só listando (dry-run)
Busca e filtra, mas **não** chama o LLM nem gera PDFs. Custa só a busca no Maps:

```powershell
python main.py -c padaria --dry-run --profundidade 20
```

Confira se apareceram padarias de Bragança Paulista e se o filtro "sem site" faz sentido. Se der erro, veja "Problemas comuns" abaixo.

### 6. Gerar as propostas
```powershell
python main.py -c padaria --limite 3
```

O programa mostra o plano (categorias, filtros e o que vai consumir créditos) e pergunta `Continuar? [s/N]`. Use `--yes` para pular a pergunta.

Para várias categorias de uma vez, repita o `-c` ou rode sem ele para usar a lista `CATEGORIES` do `.env`:

```powershell
python main.py -c padaria -c barbearia -c "pet shop" --limite 10
python main.py --limite 10
```

### 7. Onde ficam os resultados
```
output\2026-09-19_101530\
├── leads.csv          todos os estabelecimentos triados (aprovados e descartados, com o motivo)
└── propostas\
    ├── padaria-pao-quente-abc123.pdf
    └── ...
```
O `leads.csv` usa `;` como separador e abre direto no Excel em português.

## Ajustes úteis

| Quero... | Como |
|---|---|
| Buscar em outro bairro | No Google Maps, clique com o botão direito no ponto e copie as coordenadas. Coloque em `LOCATION_COORDINATE` como `latitude,longitude,zoom` (zoom `15z` ≈ um bairro, `13z` ≈ a cidade). |
| Aceitar notas menores | `--min-nota 4.2` ou `MIN_RATING` no `.env` |
| Incluir quem não tem telefone | `--permitir-sem-telefone` |
| Mais resultados por busca | `--profundidade 100` (mais caro) |
| Trocar o modelo de IA | `AISA_MODEL` no `.env` (lista em https://aisa.one/docs/guides/models) |
| Gerar sem IA | `--sem-llm` (texto padrão baseado só nos dados do Maps) |

O programa descarta endereços que não mencionem `CITY_NAME`, para não misturar cidades vizinhas.

## Como a proposta é montada (e por que é segura)

- **Preço, parcelas, prazo e validade** vêm do `.env`, nunca do LLM. A parcela é calculada no código (R$ 2.500 em 3x = R$ 833,33).
- Os **4 itens do escopo** (Página Inicial, Catálogo de Produtos, Integração WhatsApp, SEO Local) são fixos; a IA só personaliza a descrição.
- O LLM recebe **apenas fatos** (nome, categoria, nota, nº de avaliações, redes sociais encontradas) e é instruído a não inventar números, resultados ou concorrentes. Telefone e endereço não são enviados ao LLM.
- Se o LLM responder fora do formato, o programa tenta mais uma vez e, se falhar de novo, usa um **texto padrão** — o lead não se perde.
- O diagnóstico usa só dados públicos do Google Maps. O rodapé do PDF informa isso.

## Testes automáticos

```powershell
pip install -r requirements-dev.txt
python -m unittest discover -s tests -t . -v
```

Não precisam de internet nem de chave.

## Problemas comuns

| Mensagem | O que fazer |
|---|---|
| `AISA_API_KEY não configurada` | Crie o `.env` (passo 3) e preencha a chave. |
| `Chave da AIsa inválida ou ausente` (401) | Confira se copiou a chave inteira, sem espaços ou aspas. |
| `recusou a chamada por saldo/crédito` (402) ou `Sem permissão` (403) | Veja saldo e permissões da chave no console da AIsa. |
| `Rota não encontrada` (404) | A AIsa pode ter mudado a rota; confira a documentação em https://aisa.one/docs. |
| `Limite de requisições` (429) | Espere alguns minutos e rode de novo. |
| `Nenhum lead aprovado` | Reduza `--min-nota`, aumente `--profundidade` ou tente outra categoria. Veja a contagem de motivos no fim da triagem. |
| Acentos estranhos no terminal | Use o Windows Terminal/PowerShell atual, ou rode `chcp 65001` antes. |

## Uso responsável

- **Custos:** cada categoria pesquisada é uma chamada paga ao Maps (o custo cresce com `--profundidade`), e cada proposta é uma chamada paga ao LLM. Consulte os preços no console da AIsa; o programa sempre mostra o plano antes de gastar.
- **Contato comercial:** os dados são de estabelecimentos comerciais (informação pública de negócio), mas ao abordar o dono por telefone ou WhatsApp respeite a LGPD e as regras de mensagens não solicitadas: identifique-se, seja breve e ofereça a opção de não receber novos contatos.
- **Revise antes de enviar:** o texto é gerado automaticamente. Confira nome, nota e telefone no Google Maps antes de mandar.

## Estrutura do código

```
main.py                    ponto de entrada da linha de comando
webapp/                    app web (FastAPI): main.py (API), auth.py (login), jobs.py (tarefas),
                           settings.py (limites), static/ (página HTML/CSS/JS)
prospector/
  config.py                lê e valida o .env
  aisa_client.py           HTTP na AIsa: header único, erros em português, retentativas seguras
  places.py                busca no Maps, triagem e regra "sem site próprio"
  proposal.py              prompt, leitura do JSON do LLM, texto padrão, preço e parcelas
  pdf_export.py            PDF com fpdf2
  relatorio.py             leads.csv
  demo_data.py             empresas fictícias do modo --demo
  cli.py                   orquestra tudo
tests/test_prospector.py   46 testes do núcleo e da linha de comando
tests/test_webapp.py       30 testes do app web (login, limites, isolamento entre usuários, fluxo completo)
```
