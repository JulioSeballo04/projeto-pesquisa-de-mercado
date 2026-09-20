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

> O programa foi testado com respostas simuladas (87 testes automáticos). **Ainda não foi executado com uma chave real**, então na primeira execução use `--dry-run` (passo 5) para conferir se o formato dos dados bate com o esperado.

## App web (usar em qualquer aparelho)

A mesma busca, triagem, proposta e PDF, numa página que abre no navegador do celular, tablet ou computador: você escolhe as categorias, vê os leads (aprovados e descartados com o motivo), marca os que quer, gera as propostas e baixa PDF, ZIP ou planilha.

**Como funciona.** O servidor **não guarda nada entre uma chamada e outra**: cada chamada faz uma coisa só (buscar uma categoria, escrever uma proposta, montar um PDF). Quem conduz o fluxo é a página. Isso é necessário porque a Vercel executa cada requisição numa função separada, sem memória compartilhada nem processos em segundo plano. Na prática você ganha um botão **Cancelar** (para de gastar créditos no meio de um lote), e os resultados sobrevivem a recarregar a página (ficam no navegador até fechar a aba ou clicar em Sair).

**Como é protegido.**

- A chave da AIsa fica **só no servidor**; a página nunca a recebe.
- O acesso exige login com o mesmo Firebase do app de pesquisa e só entra quem estiver na lista `ALLOWED_EMAILS`. Sem essa lista o servidor nem sobe.
- Cada chamada é validada no servidor (tamanhos, tipos e limites como `MAX_DEPTH`), mesmo que alguém altere a página. Como o servidor não guarda estado, ele não consegue somar o total gasto por sessão: o teto por sessão (`MAX_CATEGORIES`, `MAX_PROPOSALS`) vale na página e o teto por chamada vale no servidor. Como só os e-mails da lista entram, isso protege contra erro e clique duplo, não contra um sócio mal-intencionado.
- Erros de conta na AIsa (chave inválida, sem saldo) **param o lote na hora**, sem insistir.

### Testar no seu computador (sem login, sem chave, sem custo)

```powershell
cd C:\projetos\prospector-agente
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:AUTH_MODE = "none"
python -m webapp
```

Abra http://127.0.0.1:8000. Sem chave da AIsa, a página entra em **modo demonstração** (empresas fictícias). O modo `AUTH_MODE=none` só aceita conexões do próprio computador; o servidor recusa subir com ele em outro endereço.

### O Firebase: o que já está pronto e o que falta

O app usa o Firebase **só para o login** (Authentication com e-mail e senha). Não usa Firestore, então nenhuma regra do banco precisa mudar. Ele reaproveita o projeto do app de pesquisa (`pesquisa-de-mercado-c87b1`) e os usuários que já existem lá.

| Já existe | Falta fazer |
|---|---|
| Projeto Firebase e Authentication com e-mail/senha ligados | Copiar `apiKey` e `appId` do `pesquisademercado/js/firebase-config.js` para as variáveis `FIREBASE_WEB_API_KEY` e `FIREBASE_APP_ID` (não são segredos) |
| Usuários cadastrados em Authentication | Listar os e-mails permitidos em `ALLOWED_EMAILS` |
| | Depois do deploy, adicionar o endereço da Vercel em Authentication > Configurações > **Domínios autorizados** |

### Publicar na Vercel

> **Atenção ao plano.** Segundo os [termos da Vercel](https://vercel.com/docs/limits/fair-use-guidelines), o plano gratuito (Hobby) é restrito a **uso pessoal e não comercial**; qualquer uso comercial exige o plano Pro. Como o Prospector serve a uma consultoria, considere o Pro. Veja o preço atual na Vercel antes de decidir.

1. Tenha este código num repositório do GitHub (o `.gitignore` já impede o envio do `.env`). Se o repositório for privado, a Vercel pede permissão de acesso na primeira vez.
2. Em vercel.com: **Add New > Project**, escolha o repositório e importe. A Vercel detecta sozinha que é FastAPI (o arquivo `app.py` da raiz é a entrada). Não precisa mudar comando de build nem pasta de saída.
3. Antes de clicar em Deploy, abra **Environment Variables** e cadastre (marque `AISA_API_KEY` como *Sensitive*):

   | Variável | Valor |
   |---|---|
   | `AUTH_MODE` | `firebase` |
   | `FIREBASE_PROJECT_ID` | `pesquisa-de-mercado-c87b1` |
   | `FIREBASE_WEB_API_KEY` | o `apiKey` do `firebase-config.js` |
   | `FIREBASE_APP_ID` | o `appId` do `firebase-config.js` |
   | `ALLOWED_EMAILS` | e-mails dos sócios, separados por vírgula |
   | `AISA_API_KEY` | sua chave da AIsa |
   | `AISA_MODEL` | ex.: `gpt-4.1` |
   | `COMPANY_NAME`, `COMPANY_CONTACT` | dados que aparecem no PDF |
   | `PRICE_TOTAL`, `INSTALLMENTS`, `DELIVERY_DAYS`, `PROPOSAL_VALIDITY_DAYS` | condições padrão da proposta |

   (`HOST` e `PORT` não são necessárias na Vercel.)
4. Clique em **Deploy**. O build passa mesmo se faltar variável (a checagem só roda quando chega a primeira visita). Se faltar alguma variável, a página trava em erro 500 ao abrir; o motivo (`Faltam no .env: ...` ou outra mensagem) fica nos **Runtime Logs**, não no log do build: no projeto na Vercel, abra a aba **Logs** (ou **Deployments → seu deploy → Functions**) e recarregue a página para ver o erro aparecer em tempo real.
5. Adicione o endereço gerado (algo como `seu-projeto.vercel.app`) nos **Domínios autorizados** do Firebase (tabela acima).
6. Abra o endereço e entre com um e-mail da lista. No celular, use "Adicionar à tela inicial" para virar um atalho.

Limites da Vercel que valem aqui: cada chamada pode durar até 120 s (configurado em `vercel.json`; a busca e o LLM levam segundos) e o corpo de cada requisição vai até 4,5 MB (um PDF tem poucos KB).

**Outras hospedagens.** Como o app não depende de estado, também roda em qualquer lugar que execute Docker (`Dockerfile` incluído) ou Python 3.10+ com `python -m webapp` (defina `HOST=0.0.0.0`).

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
python -m prospector --demo --sem-llm
```

Abra o PDF gerado em `output\<data-hora>\propostas\` para ver o layout, e o `leads.csv` para ver as regras de descarte funcionando.

### 5. Primeira busca real, só listando (dry-run)
Busca e filtra, mas **não** chama o LLM nem gera PDFs. Custa só a busca no Maps:

```powershell
python -m prospector -c padaria --dry-run --profundidade 20
```

Confira se apareceram padarias de Bragança Paulista e se o filtro "sem site" faz sentido. Se der erro, veja "Problemas comuns" abaixo.

### 6. Gerar as propostas
```powershell
python -m prospector -c padaria --limite 3
```

O programa mostra o plano (categorias, filtros e o que vai consumir créditos) e pergunta `Continuar? [s/N]`. Use `--yes` para pular a pergunta.

Para várias categorias de uma vez, repita o `-c` ou rode sem ele para usar a lista `CATEGORIES` do `.env`:

```powershell
python -m prospector -c padaria -c barbearia -c "pet shop" --limite 10
python -m prospector --limite 10
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
| Erro 500 ao abrir o app na Vercel | Falta (ou está errada) uma variável de ambiente obrigatória: `FIREBASE_PROJECT_ID`, `FIREBASE_WEB_API_KEY`, `FIREBASE_APP_ID` ou `ALLOWED_EMAILS` (modo `firebase`, o padrão). Confira em **Project Settings > Environment Variables** na Vercel e veja a mensagem exata nos **Runtime Logs** (não no log do build). |

## Uso responsável

- **Custos:** cada categoria pesquisada é uma chamada paga ao Maps (o custo cresce com `--profundidade`), e cada proposta é uma chamada paga ao LLM. Consulte os preços no console da AIsa; o programa sempre mostra o plano antes de gastar.
- **Contato comercial:** os dados são de estabelecimentos comerciais (informação pública de negócio), mas ao abordar o dono por telefone ou WhatsApp respeite a LGPD e as regras de mensagens não solicitadas: identifique-se, seja breve e ofereça a opção de não receber novos contatos.
- **Revise antes de enviar:** o texto é gerado automaticamente. Confira nome, nota e telefone no Google Maps antes de mandar.

## Estrutura do código

```
app.py                     entrada da Vercel (expõe o FastAPI `app`)
vercel.json                limite de duração e arquivos excluídos do deploy
webapp/                    app web (FastAPI, sem estado): main.py (API), schemas.py (validação),
                           auth.py (login), settings.py (limites), static/ (página HTML/CSS/JS)
prospector/
  __main__.py              linha de comando (python -m prospector)
  config.py                lê e valida o .env
  aisa_client.py           HTTP na AIsa: header único, erros em português, retentativas seguras
  places.py                busca no Maps, triagem e regra "sem site próprio"
  proposal.py              prompt, leitura do JSON do LLM, texto padrão, preço e parcelas
  pdf_export.py            PDF com fpdf2
  relatorio.py             leads.csv
  demo_data.py             empresas fictícias do modo --demo
  cli.py                   orquestra tudo
tests/test_prospector.py   46 testes do núcleo e da linha de comando
tests/test_webapp.py       41 testes do app web (login, validação, erros da AIsa, fluxo completo, entrada da Vercel)
```
