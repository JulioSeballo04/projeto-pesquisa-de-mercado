// Prospector — lógica da página.
//
// Regras deste arquivo:
//  1. Nada que vem do Google (nome, endereço...) entra na página como HTML: tudo é montado com
//     textContent (via el()), então um nome de empresa malicioso não executa código.
//  2. A chave da AIsa nunca passa por aqui. A página só fala com o nosso servidor.
"use strict";

const $ = (seletor, raiz = document) => raiz.querySelector(seletor);

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
  jobId: null,
  job: null,
  categorias: [],
  selecionados: new Set(),
  mostrarDescartados: false,
  timer: null,
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

async function api(caminho, { json, metodo } = {}) {
  const cabecalhos = {};
  const token = await estado.getToken();
  if (token) cabecalhos.Authorization = `Bearer ${token}`;
  const opcoes = { method: metodo || (json ? "POST" : "GET"), headers: cabecalhos };
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

async function baixar(caminho, nomePadrao) {
  const resposta = await api(caminho);
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
  $("#btn-sair").addEventListener("click", () => signOut(auth));

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

  renderChips();
  atualizarPlano();

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
  $("#btn-zip").addEventListener("click", () => executar("#proposta-erro", () => baixar(`/api/jobs/${estado.jobId}/proposals.zip`, "propostas.zip")));
  $("#btn-csv").addEventListener("click", () => executar("#proposta-erro", () => baixar(`/api/jobs/${estado.jobId}/leads.csv`, "leads.csv")));
}

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
  const caixa = $("#chips");
  caixa.replaceChildren(
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

async function iniciarBusca() {
  mostrarErro("#busca-erro", "");
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

  $("#btn-buscar").disabled = true;
  try {
    const resposta = await api("/api/search", {
      json: { categories: demo ? ["padaria"] : estado.categorias, ...filtros, allow_no_phone: $("#f-sem-telefone").checked, demo },
    });
    estado.jobId = (await resposta.json()).jobId;
    estado.job = null;
    estado.selecionados.clear();
    estado.ultimaRenderizacao = "";
    $("#painel-resultados").hidden = true;
    $("#painel-propostas").hidden = true;
    consultarTarefa();
  } catch (erro) {
    mostrarErro("#busca-erro", erro.message);
    $("#btn-buscar").disabled = false;
  }
}

// ---------- Acompanhamento da tarefa ----------

async function consultarTarefa(tentativasComErro = 0) {
  clearTimeout(estado.timer);
  try {
    const job = await (await api(`/api/jobs/${estado.jobId}`)).json();
    estado.job = job;
    renderTarefa(job);
    if (job.status === "running") estado.timer = setTimeout(() => consultarTarefa(), 1500);
  } catch (erro) {
    if (erro.status === 404 || erro.status === 401 || erro.status === 403 || tentativasComErro >= 4) {
      mostrarErro("#erro-tarefa", erro.message);
      $("#btn-buscar").disabled = false;
      return;
    }
    estado.timer = setTimeout(() => consultarTarefa(tentativasComErro + 1), 3000); // queda de rede: tenta de novo
  }
}

function renderTarefa(job) {
  const rodando = job.status === "running";
  $("#painel-progresso").hidden = false;
  $("#titulo-progresso").textContent = rodando
    ? job.phase === "propostas" ? "Gerando propostas..." : "Buscando..."
    : job.status === "error" ? "A busca falhou" : "Concluído";
  $("#lista-progresso").replaceChildren(...job.progress.slice(-6).map((m) => el("li", {}, m)));
  mostrarErro("#erro-tarefa", job.error);

  $("#btn-buscar").disabled = rodando;
  const temLeads = job.leads.length > 0;
  $("#painel-resultados").hidden = !temLeads;
  $("#painel-propostas").hidden = !job.leads.some((l) => l.approved);
  if (temLeads) {
    renderResumo(job.leads);
    renderLeads();
  }
  atualizarBotoes();
}

function renderResumo(leads) {
  const aprovados = leads.filter((l) => l.approved).length;
  const motivos = {};
  for (const l of leads.filter((x) => !x.approved)) motivos[l.reasonText] = (motivos[l.reasonText] || 0) + 1;
  $("#resumo").replaceChildren(
    el("span", { class: "selo selo--ok" }, `${aprovados} aprovado(s)`),
    ...Object.entries(motivos).map(([texto, n]) => el("span", { class: "selo" }, `${n} · ${texto}`))
  );
}

// ---------- Lista de leads ----------

function selecionarAprovados() {
  const { maxProposals } = estado.config.limits;
  const aprovados = estado.job.leads.filter((l) => l.approved).map((l) => l.id);
  estado.selecionados = new Set(aprovados.slice(0, maxProposals));
  if (aprovados.length > maxProposals) mostrarErro("#proposta-erro", `Selecionei os ${maxProposals} primeiros (limite por vez).`);
  renderLeads();
  atualizarBotoes();
}

function criarItemLead(l) {
  const item = el("li", { class: `lead${l.approved ? "" : " lead--descartado"}` });
  const cabecalho = el("div", { class: "lead-cab" });

  if (l.approved) {
    const caixa = el("input", { type: "checkbox", "aria-label": `Selecionar ${l.name}` });
    caixa.checked = estado.selecionados.has(l.id);
    caixa.disabled = l.proposalStatus === "gerando";
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
  if (l.socialLinks.length) contato.append(` · ${l.socialLinks.join(", ")}`);
  if (l.mapsUrl && l.mapsUrl.startsWith("https://www.google.com/maps")) {
    contato.append(" · ", el("a", { href: l.mapsUrl, target: "_blank", rel: "noopener noreferrer" }, "Ver no Maps"));
  }
  corpo.append(contato);

  if (!l.approved) {
    corpo.append(el("div", { class: "lead-linha" }, `Descartado: ${l.reasonText}.`));
  } else if (l.proposalStatus !== "nenhuma") {
    const linha = el("div", { class: "lead-estado" });
    if (l.proposalStatus === "gerando") linha.append(el("span", { class: "selo selo--warn" }, "gerando proposta..."));
    if (l.proposalStatus === "erro") linha.append(el("span", { class: "selo selo--erro" }, l.error || "falhou"));
    if (l.proposalStatus === "pronta") {
      linha.append(el("span", { class: "selo selo--ok" }, l.proposalSource === "llm" ? "proposta pronta (IA)" : "proposta pronta (texto padrão)"));
      const botao = el("button", { type: "button", class: "btn btn--sm" }, "Baixar PDF");
      botao.addEventListener("click", () => executar("#proposta-erro", () => baixar(`/api/jobs/${estado.jobId}/leads/${l.id}/pdf`, "proposta.pdf")));
      linha.append(botao);
    }
    corpo.append(linha);
  }
  item.append(corpo);
  return item;
}

function renderLeads() {
  if (!estado.job) return;
  const visiveis = estado.job.leads.filter((l) => l.approved || estado.mostrarDescartados);
  // Só reconstrói a lista se algo mudou (evita perder o foco do teclado a cada consulta).
  const assinatura = JSON.stringify([visiveis, [...estado.selecionados]]);
  if (assinatura === estado.ultimaRenderizacao) return;
  estado.ultimaRenderizacao = assinatura;
  $("#lista-leads").replaceChildren(
    ...(visiveis.length ? visiveis.map(criarItemLead) : [el("li", { class: "dica" }, "Nenhum lead aprovado. Tente outras categorias ou reduza a nota mínima.")])
  );
}

// ---------- Propostas ----------

function atualizarBotoes() {
  const job = estado.job;
  const rodando = job?.status === "running";
  const n = estado.selecionados.size;
  $("#btn-gerar").textContent = n ? `Gerar propostas (${n})` : "Gerar propostas";
  $("#btn-gerar").disabled = rodando || n === 0;
  $("#btn-zip").disabled = rodando || !job?.leads.some((l) => l.proposalStatus === "pronta");
  $("#btn-csv").disabled = !job?.leads.length;
}

async function gerarPropostas() {
  mostrarErro("#proposta-erro", "");
  const preco = lerNumero($("#p-preco").value);
  const parcelas = lerNumero($("#p-parcelas").value);
  const prazo = lerNumero($("#p-prazo").value);
  const validade = lerNumero($("#p-validade").value);
  if (!(preco > 0)) return mostrarErro("#proposta-erro", "Informe o investimento (maior que zero).");
  if (![parcelas, prazo, validade].every((v) => Number.isInteger(v) && v >= 1)) {
    return mostrarErro("#proposta-erro", "Parcelas, prazo e validade devem ser números inteiros a partir de 1.");
  }
  const usarIa = $("#p-llm").checked && !estado.job.demo;
  const n = estado.selecionados.size;
  if (usarIa && !confirm(`Gerar ${n} proposta(s) com IA?\nCada uma consome créditos do LLM da AIsa.`)) return;

  $("#btn-gerar").disabled = true;
  try {
    await api(`/api/jobs/${estado.jobId}/proposals`, {
      json: {
        lead_ids: [...estado.selecionados],
        use_llm: $("#p-llm").checked,
        price_total: preco,
        installments: parcelas,
        delivery_days: prazo,
        validity_days: validade,
      },
    });
    consultarTarefa();
  } catch (erro) {
    mostrarErro("#proposta-erro", erro.message);
    atualizarBotoes();
  }
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
