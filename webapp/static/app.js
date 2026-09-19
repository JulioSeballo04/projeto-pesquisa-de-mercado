// Prospector — lógica da página.
//
// Regras deste arquivo:
//  1. Nada que vem do Google (nome, endereço...) entra na página como HTML: tudo é montado com
//     textContent (via el()), então um nome de empresa malicioso não executa código.
//  2. A chave da AIsa nunca passa por aqui. A página só fala com o nosso servidor.
//  3. O servidor não guarda estado (roda em funções serverless). Quem conduz o fluxo é esta
//     página: uma chamada por categoria e uma por proposta. Os resultados ficam na memória
//     da página (e no sessionStorage, para sobreviver a um recarregamento).
"use strict";

const $ = (seletor, raiz = document) => raiz.querySelector(seletor);
const CHAVE_SESSAO = "prospector-sessao";

// Cria um elemento. Filhos do tipo texto viram texto puro (nunca HTML).
function el(tag, atributos = {}, ...filhos) {
  const no = document.createElement(tag);
  for (const [chave, valor] of Object.entries(atributos)) {
    if (valor === false || valor == null) continue;
    if (chave === "class") no.className = valor;
    else no.setAttribute(chave, valor === true ? "" : valor);
  }
  for (const filho of filhos.flat()) if (filho != null && filho !== false) no.append(filho);
  return no;
}

const estado = {
  config: null,
  getToken: async () => null,
  sair: null,
  leads: [], // cada lead: dados do servidor + { proposal, proposalStatus, error } (só na página)
  demo: false,
  categorias: [],
  selecionados: new Set(),
  mostrarDescartados: false,
  ocupado: false,
  cancelar: false,
  progresso: [],
  erroTarefa: "",
  tituloTarefa: "Andamento",
  appConfigurado: false,
  ultimaRenderizacao: "",
};

// ---------- Números no formato brasileiro ----------

// "4,5" -> 4.5 ; "2.500,50" -> 2500.5 ; "1.750" -> 1750 (ponto de milhar). null se vazio; NaN se inválido.
function lerNumero(texto) {
  const t = String(texto).trim();
  if (t === "") return null;
  const normal = /^\d{1,3}(\.\d{3})+(,\d+)?$/.test(t) ? t.replace(/\./g, "").replace(",", ".") : t.replace(",", ".");
  return /^\d+(\.\d+)?$/.test(normal) ? Number(normal) : NaN;
}

const numeroBR = (n, casas = 1) => n.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });

// ---------- Chamadas ao servidor ----------

class ErroApi extends Error {
  constructor(mensagem, status) {
    super(mensagem);
    this.status = status;
  }
}

async function api(caminho, { json } = {}) {
  const cabecalhos = {};
  const token = await estado.getToken();
  if (token) cabecalhos.Authorization = `Bearer ${token}`;
  const opcoes = { method: json ? "POST" : "GET", headers: cabecalhos };
  if (json) {
    cabecalhos["Content-Type"] = "application/json";
    opcoes.body = JSON.stringify(json);
  }

  let resposta;
  try {
    resposta = await fetch(caminho, opcoes);
  } catch {
    throw new ErroApi("Sem conexão com o servidor. Verifique sua internet.", 0);
  }
  if (resposta.ok) return resposta;

  let mensagem = `Erro ${resposta.status}.`;
  try {
    const dados = await resposta.json();
    if (typeof dados.detail === "string") mensagem = dados.detail;
    else if (Array.isArray(dados.detail)) mensagem = dados.detail.map((d) => d.msg).join(" ");
  } catch {
    /* corpo sem JSON: mantém a mensagem padrão */
  }
  if (estado.config?.authMode === "firebase" && (resposta.status === 401 || resposta.status === 403)) {
    if (estado.sair) await estado.sair();
    mostrarLogin(mensagem);
  }
  throw new ErroApi(mensagem, resposta.status);
}

// 424 = a AIsa recusou por conta/chave/rota: repetir a chamada não adianta.
const erroFatal = (erro) => erro.status === 424 || erro.status === 401 || erro.status === 403;

async function baixar(caminho, json, nomePadrao) {
  const resposta = await api(caminho, { json });
  const blob = await resposta.blob();
  const disposicao = resposta.headers.get("Content-Disposition") || "";
  const nome = /filename="([^"]+)"/.exec(disposicao)?.[1] || nomePadrao;
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: nome });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 15000);
}

function mostrarErro(seletor, mensagem) {
  const caixa = $(seletor);
  caixa.textContent = mensagem || "";
  caixa.hidden = !mensagem;
}

// ---------- Sessão (sobrevive a recarregar a página) ----------

function salvarSessao() {
  try {
    sessionStorage.setItem(CHAVE_SESSAO, JSON.stringify({ leads: estado.leads, demo: estado.demo }));
  } catch {
    /* sem armazenamento (modo privado, cheio...): segue só em memória */
  }
}

function restaurarSessao() {
  try {
    const dados = JSON.parse(sessionStorage.getItem(CHAVE_SESSAO) || "null");
    if (!dados || !Array.isArray(dados.leads)) return;
    estado.demo = Boolean(dados.demo);
    // Uma proposta "gerando" quando a página foi recarregada nunca terminou: volta a "nenhuma".
    estado.leads = dados.leads.map((l) => (l.proposalStatus === "gerando" ? { ...l, proposalStatus: "nenhuma" } : l));
  } catch {
    estado.leads = [];
  }
}

// ---------- Login (Firebase) ----------

function mostrarLogin(mensagem = "") {
  $("#tela-app").hidden = true;
  $("#tela-login").hidden = false;
  $("#btn-sair").hidden = true;
  $("#usuario-email").textContent = "";
  mostrarErro("#login-erro", mensagem);
}

function mostrarApp(rotuloUsuario) {
  $("#tela-login").hidden = true;
  $("#tela-app").hidden = false;
  $("#usuario-email").textContent = rotuloUsuario;
  $("#btn-sair").hidden = estado.config.authMode !== "firebase";
  configurarApp();
}

const ERROS_LOGIN = {
  "auth/invalid-credential": "E-mail ou senha incorretos.",
  "auth/user-not-found": "E-mail ou senha incorretos.",
  "auth/wrong-password": "E-mail ou senha incorretos.",
  "auth/invalid-email": "E-mail inválido.",
  "auth/too-many-requests": "Muitas tentativas. Aguarde um pouco e tente de novo.",
  "auth/network-request-failed": "Sem conexão com a internet.",
};

async function iniciarFirebase(configFirebase) {
  const base = "https://www.gstatic.com/firebasejs/10.13.0/";
  let modulos;
  try {
    modulos = await Promise.all([import(`${base}firebase-app.js`), import(`${base}firebase-auth.js`)]);
  } catch {
    mostrarLogin("Não foi possível carregar o login (sem internet?). Recarregue a página.");
    return;
  }
  const [{ initializeApp }, { getAuth, signInWithEmailAndPassword, onAuthStateChanged, signOut }] = modulos;
  const auth = getAuth(initializeApp(configFirebase));

  estado.getToken = async () => (auth.currentUser ? auth.currentUser.getIdToken() : null);
  estado.sair = () => signOut(auth);

  $("#form-login").addEventListener("submit", async (evento) => {
    evento.preventDefault();
    mostrarErro("#login-erro", "");
    const botao = $("#btn-entrar");
    botao.disabled = true;
    botao.textContent = "Entrando...";
    try {
      await signInWithEmailAndPassword(auth, $("#login-email").value.trim(), $("#login-senha").value);
    } catch (erro) {
      mostrarErro("#login-erro", ERROS_LOGIN[erro.code] || "Não foi possível entrar. Tente novamente.");
    } finally {
      botao.disabled = false;
      botao.textContent = "Entrar";
    }
  });
  $("#btn-sair").addEventListener("click", async () => {
    try {
      sessionStorage.removeItem(CHAVE_SESSAO); // ao sair, os dados de empresas não ficam no aparelho
    } catch {
      /* ignora */
    }
    await signOut(auth);
  });

  onAuthStateChanged(auth, (usuario) => {
    if (usuario) mostrarApp(usuario.email);
    else mostrarLogin();
  });
}

// ---------- Tela principal ----------

function configurarApp() {
  if (estado.appConfigurado) return;
  estado.appConfigurado = true;
  const { defaults, limits, hasApiKey } = estado.config;

  estado.categorias = [...defaults.categories].slice(0, limits.maxCategories);
  $("#f-nota").value = numeroBR(defaults.minRating);
  $("#f-avaliacoes").value = String(defaults.minReviews);
  $("#f-profundidade").value = String(defaults.depth);
  $("#p-preco").value = numeroBR(defaults.priceTotal, 2);
  $("#p-parcelas").value = String(defaults.installments);
  $("#p-prazo").value = String(defaults.deliveryDays);
  $("#p-validade").value = String(defaults.validityDays);
  $("#empresa-proposta").textContent = `Proposta em nome de "${estado.config.company.name}" (definido no servidor).`;

  if (!hasApiKey) {
    const aviso = $("#aviso-config");
    aviso.textContent = "A chave da AIsa não está configurada no servidor: por enquanto só o modo demonstração funciona.";
    aviso.hidden = false;
    $("#f-demo").checked = true;
  }

  restaurarSessao();
  renderChips();
  atualizarPlano();
  renderTudo();

  $("#btn-add-cat").addEventListener("click", adicionarCategorias);
  $("#cat-entrada").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      adicionarCategorias();
    }
  });
  $("#chips").addEventListener("click", (e) => {
    const botao = e.target.closest("button[data-cat]");
    if (!botao) return;
    estado.categorias = estado.categorias.filter((c) => c !== botao.dataset.cat);
    renderChips();
    atualizarPlano();
  });
  for (const id of ["#f-profundidade", "#f-demo"]) $(id).addEventListener("input", atualizarPlano);

  $("#form-busca").addEventListener("submit", (e) => {
    e.preventDefault();
    iniciarBusca();
  });
  $("#btn-cancelar").addEventListener("click", () => {
    estado.cancelar = true;
    $("#btn-cancelar").disabled = true;
  });
  $("#mostrar-descartados").addEventListener("change", (e) => {
    estado.mostrarDescartados = e.target.checked;
    renderLeads();
  });
  $("#btn-sel-todos").addEventListener("click", selecionarAprovados);
  $("#btn-sel-nenhum").addEventListener("click", () => {
    estado.selecionados.clear();
    renderLeads();
    atualizarBotoes();
  });
  $("#btn-gerar").addEventListener("click", gerarPropostas);
  $("#btn-zip").addEventListener("click", () =>
    executar("#proposta-erro", async () => {
      const prontas = estado.leads.filter((l) => l.proposal);
      await baixar("/api/zip", { items: prontas.map((l) => ({ lead: carga(l), proposal: l.proposal })), ...lerOpcoes() }, "propostas.zip");
    })
  );
  $("#btn-csv").addEventListener("click", () =>
    executar("#proposta-erro", () => baixar("/api/csv", { leads: estado.leads.map(carga) }, "leads.csv"))
  );
}

// Dados do lead como o servidor espera (o servidor ignora campos que só existem na página).
const carga = (l) => ({ ...l, proposal: undefined, proposal_source: l.proposal?.source ?? null });

// Executa uma ação e mostra o erro (se houver) na caixa indicada.
async function executar(seletorErro, acao) {
  mostrarErro(seletorErro, "");
  try {
    await acao();
  } catch (erro) {
    mostrarErro(seletorErro, erro.message);
  }
}

function renderChips() {
  $("#chips").replaceChildren(
    ...estado.categorias.map((c) =>
      el("span", { class: "chip" }, c, el("button", { type: "button", "data-cat": c, "aria-label": `Remover ${c}` }, "×"))
    )
  );
}

function adicionarCategorias() {
  const campo = $("#cat-entrada");
  const { maxCategories } = estado.config.limits;
  mostrarErro("#busca-erro", "");
  for (const bruto of campo.value.split(",")) {
    const nome = bruto.trim().replace(/\s+/g, " ");
    if (!nome) continue;
    if (nome.length > 60) {
      mostrarErro("#busca-erro", "Cada categoria pode ter no máximo 60 caracteres.");
      continue;
    }
    if (estado.categorias.some((c) => c.toLowerCase() === nome.toLowerCase())) continue;
    if (estado.categorias.length >= maxCategories) {
      mostrarErro("#busca-erro", `No máximo ${maxCategories} categorias por busca.`);
      break;
    }
    estado.categorias.push(nome);
  }
  campo.value = "";
  renderChips();
  atualizarPlano();
}

function atualizarPlano() {
  const demo = $("#f-demo").checked;
  const profundidade = lerNumero($("#f-profundidade").value);
  $("#plano").textContent = demo
    ? "Modo demonstração: nenhuma chamada paga. As categorias são fixas (padaria, barbearia, salão de beleza, restaurante e academia)."
    : `${estado.categorias.length} busca(s) no Google Maps (até ${Number.isFinite(profundidade) ? profundidade : "?"} resultados cada). Isso consome créditos da AIsa.`;
}

function lerFiltros() {
  const nota = lerNumero($("#f-nota").value);
  const avaliacoes = lerNumero($("#f-avaliacoes").value);
  const profundidade = lerNumero($("#f-profundidade").value);
  const { maxDepth } = estado.config.limits;
  if (!Number.isFinite(nota) || nota < 0 || nota > 5) throw new Error("A nota mínima deve estar entre 0 e 5.");
  if (!Number.isInteger(avaliacoes) || avaliacoes < 0) throw new Error("O mínimo de avaliações deve ser um número inteiro (0 ou mais).");
  if (!Number.isInteger(profundidade) || profundidade < 1 || profundidade > maxDepth) {
    throw new Error(`Os resultados por categoria devem estar entre 1 e ${maxDepth}.`);
  }
  return { min_rating: nota, min_reviews: avaliacoes, depth: profundidade };
}

function lerOpcoes() {
  const preco = lerNumero($("#p-preco").value);
  const parcelas = lerNumero($("#p-parcelas").value);
  const prazo = lerNumero($("#p-prazo").value);
  const validade = lerNumero($("#p-validade").value);
  if (!(preco > 0)) throw new Error("Informe o investimento (maior que zero).");
  if (![parcelas, prazo, validade].every((v) => Number.isInteger(v) && v >= 1)) {
    throw new Error("Parcelas, prazo e validade devem ser números inteiros a partir de 1.");
  }
  return { price_total: preco, installments: parcelas, delivery_days: prazo, validity_days: validade };
}

// ---------- Andamento (busca e geração rodam aqui, uma chamada por vez) ----------

function log(mensagem) {
  estado.progresso.push(mensagem);
  estado.progresso = estado.progresso.slice(-8);
  renderTarefa();
}

function definirOcupado(ocupado, titulo) {
  estado.ocupado = ocupado;
  if (titulo) estado.tituloTarefa = titulo;
  if (ocupado) {
    estado.cancelar = false;
    $("#btn-cancelar").disabled = false;
  }
  renderTudo();
}

async function iniciarBusca() {
  mostrarErro("#busca-erro", "");
  if (estado.ocupado) return;
  const demo = $("#f-demo").checked;
  let filtros;
  try {
    filtros = lerFiltros();
  } catch (erro) {
    mostrarErro("#busca-erro", erro.message);
    return;
  }
  if (!demo && estado.categorias.length === 0) {
    mostrarErro("#busca-erro", "Adicione ao menos uma categoria.");
    return;
  }
  if (!demo && !confirm(`Buscar ${estado.categorias.length} categoria(s) no Google Maps?\nIsso consome créditos da AIsa.`)) return;

  estado.leads = [];
  estado.demo = demo;
  estado.selecionados.clear();
  estado.progresso = [];
  estado.erroTarefa = "";
  estado.ultimaRenderizacao = "";
  definirOcupado(true, "Buscando...");

  // No modo demonstração uma única chamada devolve todos os dados fictícios.
  const categorias = demo ? ["demonstração"] : [...estado.categorias];
  const vistos = new Set();
  try {
    for (const categoria of categorias) {
      if (estado.cancelar) {
        log("Busca cancelada.");
        break;
      }
      log(demo ? "Carregando os dados de demonstração..." : `Buscando '${categoria}' no Google Maps...`);
      const dados = await (
        await api("/api/search", { json: { category: demo ? "demo" : categoria, ...filtros, allow_no_phone: $("#f-sem-telefone").checked, demo } })
      ).json();
      let novos = 0;
      for (const lead of dados.leads) {
        if (vistos.has(lead.id)) continue; // o mesmo local pode aparecer em mais de uma categoria
        vistos.add(lead.id);
        estado.leads.push({ ...lead, proposal: null, proposalStatus: "nenhuma", error: null });
        novos += 1;
      }
      log(`'${categoria}': ${dados.leads.length} resultado(s), ${novos} novo(s).`);
      renderTudo();
    }
    const aprovados = estado.leads.filter((l) => l.approved).length;
    if (!estado.cancelar) log(`Pronto: ${estado.leads.length} estabelecimento(s) triado(s), ${aprovados} aprovado(s).`);
  } catch (erro) {
    estado.erroTarefa = erro.message;
  } finally {
    estado.tituloTarefa = estado.erroTarefa ? "A busca falhou" : "Concluído";
    salvarSessao();
    definirOcupado(false);
  }
}

async function gerarPropostas() {
  mostrarErro("#proposta-erro", "");
  if (estado.ocupado) return;
  try {
    lerOpcoes(); // só para validar antes de começar
  } catch (erro) {
    mostrarErro("#proposta-erro", erro.message);
    return;
  }
  const escolhidos = estado.leads.filter((l) => estado.selecionados.has(l.id));
  const usarIa = $("#p-llm").checked && !estado.demo;
  if (usarIa && !confirm(`Gerar ${escolhidos.length} proposta(s) com IA?\nCada uma consome créditos do LLM da AIsa.`)) return;

  estado.progresso = [];
  estado.erroTarefa = "";
  definirOcupado(true, "Gerando propostas...");
  let parar = null;
  try {
    for (const lead of escolhidos) {
      if (estado.cancelar) {
        log("Geração cancelada.");
        break;
      }
      lead.proposalStatus = "gerando";
      lead.error = null;
      log(`Gerando proposta de '${lead.name}'...`);
      renderTudo();
      try {
        const dados = await (await api("/api/proposal", { json: { lead: carga(lead), use_llm: usarIa } })).json();
        lead.proposal = dados.proposal;
        lead.proposalStatus = "pronta";
      } catch (erro) {
        lead.proposalStatus = "erro";
        lead.error = erro.message;
        if (erroFatal(erro)) {
          parar = erro.message; // conta/chave/rota: as próximas também falhariam
          break;
        }
      }
      salvarSessao();
    }
    if (!estado.cancelar && !parar) log("Propostas concluídas.");
  } finally {
    // Leads que ficaram "gerando" por causa de um cancelamento ou de uma falha fatal voltam ao normal.
    for (const l of escolhidos) if (l.proposalStatus === "gerando") l.proposalStatus = "nenhuma";
    estado.erroTarefa = parar || "";
    estado.tituloTarefa = parar ? "A geração parou" : "Concluído";
    salvarSessao();
    definirOcupado(false);
  }
}

// ---------- Desenho da tela ----------

function renderTudo() {
  renderTarefa();
  const temLeads = estado.leads.length > 0;
  $("#painel-resultados").hidden = !temLeads;
  $("#painel-propostas").hidden = !estado.leads.some((l) => l.approved);
  if (temLeads) {
    renderResumo();
    renderLeads();
  }
  atualizarBotoes();
}

function renderTarefa() {
  const visivel = estado.ocupado || estado.progresso.length > 0 || Boolean(estado.erroTarefa);
  $("#painel-progresso").hidden = !visivel;
  $("#titulo-progresso").textContent = estado.ocupado ? estado.tituloTarefa : estado.tituloTarefa || "Andamento";
  $("#lista-progresso").replaceChildren(...estado.progresso.slice(-6).map((m) => el("li", {}, m)));
  $("#btn-cancelar").hidden = !estado.ocupado;
  mostrarErro("#erro-tarefa", estado.erroTarefa);
  $("#btn-buscar").disabled = estado.ocupado;
}

function renderResumo() {
  const aprovados = estado.leads.filter((l) => l.approved).length;
  const motivos = {};
  for (const l of estado.leads.filter((x) => !x.approved)) motivos[l.reason_text] = (motivos[l.reason_text] || 0) + 1;
  $("#resumo").replaceChildren(
    el("span", { class: "selo selo--ok" }, `${aprovados} aprovado(s)`),
    ...Object.entries(motivos).map(([texto, n]) => el("span", { class: "selo" }, `${n} · ${texto}`))
  );
}

function selecionarAprovados() {
  const { maxProposals } = estado.config.limits;
  const aprovados = estado.leads.filter((l) => l.approved).map((l) => l.id);
  estado.selecionados = new Set(aprovados.slice(0, maxProposals));
  mostrarErro("#proposta-erro", aprovados.length > maxProposals ? `Selecionei os ${maxProposals} primeiros (limite por vez).` : "");
  renderLeads();
  atualizarBotoes();
}

function criarItemLead(l) {
  const item = el("li", { class: `lead${l.approved ? "" : " lead--descartado"}` });
  const cabecalho = el("div", { class: "lead-cab" });

  if (l.approved) {
    const caixa = el("input", { type: "checkbox", "aria-label": `Selecionar ${l.name}` });
    caixa.checked = estado.selecionados.has(l.id);
    caixa.disabled = estado.ocupado;
    caixa.addEventListener("change", () => {
      const { maxProposals } = estado.config.limits;
      if (caixa.checked && estado.selecionados.size >= maxProposals) {
        caixa.checked = false;
        mostrarErro("#proposta-erro", `No máximo ${maxProposals} propostas por vez.`);
        return;
      }
      mostrarErro("#proposta-erro", "");
      if (caixa.checked) estado.selecionados.add(l.id);
      else estado.selecionados.delete(l.id);
      atualizarBotoes();
    });
    cabecalho.append(caixa);
  }

  const nota = l.rating == null ? "sem nota" : `nota ${numeroBR(l.rating)} (${l.reviews ?? 0} avaliações)`;
  cabecalho.append(
    el("div", {}, el("div", { class: "lead-nome" }, l.name), el("div", { class: "lead-meta" }, [nota, l.category].filter(Boolean).join(" · ")))
  );
  item.append(cabecalho);

  const corpo = el("div", { class: "lead-corpo" });
  if (l.address) corpo.append(el("div", { class: "lead-linha" }, l.address));

  const contato = el("div", { class: "lead-linha" });
  if (l.phone) {
    const digitos = l.phone.replace(/[^\d+]/g, "");
    contato.append(digitos ? el("a", { href: `tel:${digitos}` }, l.phone) : l.phone);
  } else {
    contato.append("sem telefone");
  }
  if (l.social_links.length) contato.append(` · ${l.social_links.join(", ")}`);
  if (l.maps_url && l.maps_url.startsWith("https://www.google.com/maps")) {
    contato.append(" · ", el("a", { href: l.maps_url, target: "_blank", rel: "noopener noreferrer" }, "Ver no Maps"));
  }
  corpo.append(contato);

  if (!l.approved) {
    corpo.append(el("div", { class: "lead-linha" }, `Descartado: ${l.reason_text}.`));
  } else if (l.proposalStatus !== "nenhuma") {
    const linha = el("div", { class: "lead-estado" });
    if (l.proposalStatus === "gerando") linha.append(el("span", { class: "selo selo--warn" }, "gerando proposta..."));
    if (l.proposalStatus === "erro") linha.append(el("span", { class: "selo selo--erro" }, l.error || "falhou"));
    if (l.proposalStatus === "pronta") {
      linha.append(el("span", { class: "selo selo--ok" }, l.proposal.source === "llm" ? "proposta pronta (IA)" : "proposta pronta (texto padrão)"));
      const botao = el("button", { type: "button", class: "btn btn--sm" }, "Baixar PDF");
      botao.addEventListener("click", () =>
        executar("#proposta-erro", () => baixar("/api/pdf", { lead: carga(l), proposal: l.proposal, ...lerOpcoes() }, "proposta.pdf"))
      );
      linha.append(botao);
    }
    corpo.append(linha);
  }
  item.append(corpo);
  return item;
}

function renderLeads() {
  const visiveis = estado.leads.filter((l) => l.approved || estado.mostrarDescartados);
  // Só reconstrói a lista se algo mudou (evita perder o foco do teclado a cada atualização).
  const assinatura = JSON.stringify([visiveis, [...estado.selecionados], estado.ocupado]);
  if (assinatura === estado.ultimaRenderizacao) return;
  estado.ultimaRenderizacao = assinatura;
  $("#lista-leads").replaceChildren(
    ...(visiveis.length ? visiveis.map(criarItemLead) : [el("li", { class: "dica" }, "Nenhum lead aprovado. Tente outras categorias ou reduza a nota mínima.")])
  );
}

function atualizarBotoes() {
  const n = estado.selecionados.size;
  $("#btn-gerar").textContent = n ? `Gerar propostas (${n})` : "Gerar propostas";
  $("#btn-gerar").disabled = estado.ocupado || n === 0;
  $("#btn-zip").disabled = estado.ocupado || !estado.leads.some((l) => l.proposal);
  $("#btn-csv").disabled = estado.ocupado || estado.leads.length === 0;
}

// ---------- Início ----------

async function iniciar() {
  try {
    const resposta = await fetch("/api/config");
    if (!resposta.ok) throw new Error();
    estado.config = await resposta.json();
  } catch {
    $("#carregando").textContent = "Não foi possível falar com o servidor. Recarregue a página.";
    return;
  }
  $("#cidade").textContent = `· ${estado.config.city}`;
  $("#carregando").hidden = true;

  if (estado.config.authMode === "firebase") await iniciarFirebase(estado.config.firebase);
  else mostrarApp("uso local (sem login)");
}

iniciar();
