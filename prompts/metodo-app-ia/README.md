# Método de criação de produto digital com IA

Biblioteca de prompts reutilizável para construir aplicativos/sites com ferramentas de IA
(Lovable, Claude, etc.) seguindo um processo estruturado, em vez de um único prompt genérico
("Crie um site para X"). A lógica central:

```
1. Entrevista → 2. Briefing → 3. Design System → 4. Arquitetura
→ 5. Banco de dados → 6. Implementação → 7. Revisão de design → 8. QA
```

A ideia é entender o negócio, o público e o problema **antes** de qualquer linha de código —
"entenda o negócio → entenda o cliente → defina a experiência → defina a identidade →
construa → revise", em vez de pular direto para a implementação.

## Como usar

Use os arquivos em sequência, colando o prompt correspondente na ferramenta de IA (Lovable,
Claude, etc.) e respondendo/revisando antes de avançar para o próximo:

| Arquivo | Fase | O que faz |
|---|---|---|
| `01_ENTREVISTA.md` | Descoberta | A IA faz uma entrevista estratégica sobre o negócio, público, produto, conversão e identidade — sem construir nada ainda |
| `02_BRIEFING.md` | Requisitos | Transforma as respostas da entrevista num Product Brief completo e organizado |
| `03_DESIGN_SYSTEM.md` | UI | Define cores, tipografia, espaçamento, bordas, sombras, animações e componentes como tokens reutilizáveis |
| `04_ARQUITETURA.md` | Arquitetura | Define páginas, rotas, componentes, fluxos e estados (loading/vazio/erro) — ainda sem implementar |
| `05_BANCO_DE_DADOS.md` | Dados | Projeta collections/documentos (Firebase Firestore), regras de segurança e estratégia de autenticação |
| `06_IMPLEMENTACAO.md` | Construção | Só agora a IA constrói, usando tudo que foi definido nas fases anteriores |
| `07_REVISAO_DESIGN.md` | Refinamento | A IA assume o papel de diretor de arte/UX sênior e corrige hierarquia, espaçamento, consistência — para não parecer "genérico de IA" |
| `08_QA.md` | Testes | Auditoria de autenticação, CRUD, UX, responsividade, navegação, segurança e performance |
| `09_MASTER_PROMPT.md` | Tudo-em-um | Versão consolidada das 8 fases num único prompt, para quem prefere um ponto de partida só |

A pasta `exemplos/` tem o mesmo método aplicado a domínios específicos (manutenção industrial,
sistema de transportadores), mostrando como adaptar as perguntas da entrevista para cada tipo
de produto.

## Regra de ouro em todas as fases

Nunca inventar informação factual sobre o negócio, cliente ou produto — se faltar dado, a IA
deve perguntar, não presumir. Essa regra está escrita explicitamente em quase todos os prompts
abaixo; é o que evita um app com conteúdo genérico ou inventado.

## Ainda não escritos (ideias para quando surgir a necessidade)

A estrutura original também previa arquivos para UX dedicado, Dashboard, Responsividade e
Deploy como prompts separados. Não foram criados ainda porque o conteúdo de origem não
detalhava esses prompts especificamente — hoje essas partes estão cobertas, em menor
profundidade, dentro de `04_ARQUITETURA.md` (UX/fluxos), `06_IMPLEMENTACAO.md`
(responsividade) e `08_QA.md` (responsividade e QA). Vale separá-los em arquivos próprios
quando um projeto real precisar de mais detalhe neles.
