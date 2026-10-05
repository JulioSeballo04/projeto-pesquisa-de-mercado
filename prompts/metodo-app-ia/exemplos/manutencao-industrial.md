# Exemplo — Sistema de manutenção industrial

Mostra como adaptar a `01_ENTREVISTA.md` genérica para um domínio técnico específico, em vez de
pedir direto "crie um sistema de manutenção".

## Perguntas de entrevista adaptadas ao domínio

```
Qual é o problema atual da manutenção?

Como uma OS é aberta?

Quem abre?

Quem executa?

Quem aprova?

Como são registradas as peças?

Como é registrado o tempo de máquina parada?

Como funciona a preventiva?

Como funciona a corretiva?

Quais equipamentos existem?

Como são identificados?

Quais indicadores precisam aparecer?

MTBF?

MTTR?

Disponibilidade?

Backlog?

Quais usuários existem?

Mecânico?

Planejador?

Supervisor?

Gestor?
```

## Esboço do produto resultante

```
Frontend: HTML/CSS/JS ou React
Backend: Firebase
Autenticação: Firebase Auth
Banco: Firestore

Dashboard
- OS abertas
- OS concluídas
- equipamentos
- paradas
- MTTR
- MTBF
- disponibilidade

Módulos
- Equipamentos
- Ordens de serviço
- Preventivas
- Corretivas
- Mecânicos
- Peças
- Histórico
- Indicadores
```
