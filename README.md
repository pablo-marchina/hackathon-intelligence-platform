# Hackathon Intelligence Platform

> **Checkpoint Iteração 25 (21/10/2026):** diagnóstico read-only por fonte, histórico real de `crawl_runs`, causas de rejeição agrupadas sem erros brutos e triagem consultiva para expansão da cobertura. `GET /admin/source-insights`, painel `/admin` e `python scripts/source_insights_report.py --format markdown` (PostgreSQL migrado). **444 testes locais aprovados**; novos eventos reais, integração PostgreSQL e build Next.js não validados. Consulte `docs/execution/ITERATION_25_2026-10-21.md`. **Ainda não aprovado para produção.**

> **Checkpoint Iteração 24 (21/10/2026):** painel de conversão da ingestão por fonte, separando tarefas de URLs, referências de eventos, payloads duráveis, normalizações aceitas/rejeitadas e hackathons ativos vinculados. Totais globais deduplicam hackathons entre fontes; percentuais usam apenas denominadores compatíveis. **436 testes locais aprovados**; PostgreSQL real, build Next.js e novas coletas externas ainda não verificados. Consulte `docs/execution/ITERATION_24_2026-10-21.md`. **Não aprovado para produção.**


> **Checkpoint Iteração 23 (21/10/2026):** série diária UTC de hackathons canônicos inseridos no banco, contagens de novas referências e canonicals atribuídos por fonte, disponíveis em `/admin/coverage?recent_days=7`, no painel e no CLI. Os resultados abrangem também importações históricas: não representam necessariamente eventos recém-anunciados ou novas descobertas externas. **431 testes locais aprovados**; integração real PostgreSQL e frontend build pendentes. Veja `docs/execution/ITERATION_23_2026-10-21.md`. **Não liberado para produção.**

> **Checkpoint Iteração 21 (21/10/2026):** paginação durável de RSS/sitemaps sem perder URLs além do limite por XML; cursor/hash transacionais, ordem da fila que favorece URLs de detalhes, refresco de seeds com intervalo e novo exercício E2E em PostgreSQL descartável no CI. **422 testes locais aprovados**; teste E2E com PostgreSQL e coleta externa ainda não executados nesta máquina. Migração `0012_syndication_continuation` requerida antes de `/ready` e do runner persistente. `docs/execution/ITERATION_21_2026-10-21.md`. **Não liberado para produção**.


> **Checkpoint Iteração 20 (19/10/2026):** fila PostgreSQL de URLs por fonte para RSS/Atom/sitemaps, retomada entre execuções, claims protegidos e limites XML ampliados. **413 testes Python locais aprovados**; PostgreSQL real, frontend e coleta externa pendentes. A migração `0011_source_url_backlog` é necessária antes de executar o runner persistente ou `/ready`. Consulte `docs/execution/ITERATION_20_2026-10-19.md`. **Não está pronta para produção**.


Production-oriented monorepo for discovering, ingesting, reconciling, searching, and recommending hackathons globally.

> **Checkpoint local (19/10/2026): Iteração 19.** O SDK inclui descoberta de páginas via RSS/Atom/sitemap para **fontes explicitamente autorizadas**, usando JSON-LD em páginas no mesmo host. Não houve autorização de novas fontes ou execução de coletas externas; a cobertura histórica de 250 eventos permanece sem atualização. A Iteração 19 não foi publicada no GitHub nem aprovada para produção. Veja [`docs/execution/ITERATION_19_2026-10-19.md`](docs/execution/ITERATION_19_2026-10-19.md).

## Fontes RSS/Atom e sitemaps (Iteração 19)

Fontes com `ingestion_mode=RSS` ou `SITEMAP`, `permission_status=ALLOWED` e `detail_extraction_enabled=true` podem descobrir páginas por feeds XML autorizados e coletar `schema.org/Event` nessas páginas sem seguir links para terceiros. O modo RSS **exige robots.txt para páginas de detalhes**. Consulte o runbook da Iteração 19 para configurar fontes em modo `REVIEW_REQUIRED` por padrão, definir hosts permitidos e testar em staging. Listagens maiores que o orçamento de páginas ainda requerem implementação de backlog persistente para cobertura completa.

> **Checkpoint local mais recente (19/10/2026):** Iteração 18, baseada na Iteração 17; **391 testes Python locais aprovados**. O código ainda não foi sincronizado com o GitHub, e a plataforma não está aprovada para produção. PostgreSQL/Redis/Celery e o build Next.js continuam pendentes. Veja `docs/execution/ITERATION_18_2026-10-19.md` e [GITHUB_PUBLICACAO.md](GITHUB_PUBLICACAO.md). Os baselines mais antigos abaixo são históricos.


> **Cobertura global — nova prioridade (19/10/2026):** maximizar hackathons únicos, fontes legítimas, metadados verificáveis e filtros. A Iteração 16 incorpora extração genérica de eventos `schema.org` em HTML **autorizado**, filtros avançados na busca e auditoria offline do catálogo de fontes. Neste checkpoint: **23 fontes configuradas; somente 4 elegíveis a fetch por configuração; 19 em revisão**. Isso **não** implica novas coletas reais nem aprovação para produção. Veja [`docs/product/COVERAGE_FIRST_PLAN.md`](docs/product/COVERAGE_FIRST_PLAN.md).

Para auditar o backlog de fontes sem fazer chamadas de rede:

```bash
python scripts/source_coverage_report.py --format markdown
```

## Ingestão incremental de fontes autorizadas (Iteração 18)

O coletor persistente agora grava `source_event_payloads` de forma transacional com cada `source_event_ref`. Após aplicar a migração `0010_incremental_canonical_pipeline`, execute explicitamente `python -m scripts.run_incremental_canonical --apply --limit 100` para normalizar snapshots **já coletados**, sem iniciar crawls. Sem `--apply`, o comando permanece desativado. Consulte `docs/execution/ITERATION_18_2026-10-19.md` para gates, riscos e limitações. O endpoint `/ready` exige a nova revisão. Não use em produção sem staging.

## Medir cobertura real (Iteração 17)

- `python scripts/source_coverage_report.py --format json`: inventário **configurado**, não eventos coletados.
- `python scripts/catalog_coverage_report.py --format json`: contagem **real** de canônicos no PostgreSQL migrado, sem substituir por W2 histórico se a conexão falhar.
- `GET /admin/coverage`: métricas da base atual protegidas por autenticação administrativa, também exibidas no painel `/admin`.
- `docs/execution/ITERATION_17_2026-10-19.md`: implementações, testes locais e gates ainda pendentes.

**Importante:** 250 hackathons é o resultado histórico da fase W2, não o catálogo atual validado em produção. A coleta periódica permanece desligada por padrão.

> **Iteração 22 (21/10/2026):** relatório operacional de cobertura ampliado com backlog de descoberta, estágio de normalização e completude dos campos usados nos filtros. Os testes locais não equivalem a uma coleta real. Veja `docs/execution/ITERATION_22_2026-10-21.md`.

## Repository layout

- `apps/api` — FastAPI service.
- `apps/worker` — background worker service.
- `apps/web` — Next.js frontend.
- `packages/shared` — shared Python contracts/utilities.
- `db` — Alembic configuration, migrations, and PostgreSQL physical schema.
- `infra/docker` — local infrastructure.
- `scripts` — verification and developer automation.

## Local development

1. Copy `.env.example` to `.env`.
2. Start infrastructure with `docker compose -f infra/docker/compose.yml up -d`.
3. Create a Python 3.11+ virtualenv and install `.[dev]`.
4. Run migrations: `alembic -c db/alembic.ini upgrade head`.
5. Run API: `uvicorn hackathon_api.main:app --reload --app-dir apps/api/src`.
6. Run worker: `celery -A hackathon_worker.app:celery_app worker --loglevel=INFO`.
7. In `apps/web`, run `npm install && npm run dev` (this checkpoint has no lockfile yet).

## Quality gates

`make verify` runs Python formatting/lint/type/tests and repository-level static verification.

> Runtime PostgreSQL/Docker verification requires Docker and PostgreSQL client/runtime to be installed on the host.

## Current execution baseline

Implementation has started across W0–W6. See `docs/IMPLEMENTATION_STATUS.md` and `docs/execution/` for acceptance status by wave. `Done` means the relevant acceptance behavior has been verified; `Blocked` means an external runtime/capacity prerequisite is unavailable; `Review` is reserved for implemented work still awaiting an acceptance gate.

Current local verification baseline:

```text
232 pytest tests passing (W6 iteration 9 local run, 2026-10-15)
Historical PostgreSQL 16 migration cycle through 0005 verified; 0006/0007 still require new real-run evidence
PostgreSQL SQL schema: 74 tables, 5 required extensions (static verifier; not a live migration check)
source SDK: 4 authorized live API adapters registered
unified eval runner: passing
editable Python package install: passing
```

The PostgreSQL migration round-trip gate has been validated on a real PostgreSQL 16 database. W1 has four authorized live connectors and the Python runner produced complete `crawl_runs → fetch_attempts → raw_documents → source_event_refs` chains for all four sources, totaling 605 source-event refs. W2 replayed those 605 refs end-to-end into 7,346 field observations, 251 hackathon-like candidates, and 250 active canonical hackathons. W2 is now fully closed: 25 recurring event series / 60 editions, a 470-pair dedup golden set with 240/240 correct AUTO_MERGE decisions and a 98.4246% Wilson lower bound, plus real transactional split/undo/re-merge verification on Sierra Hacks. W3 is now fully closed: 10-language versioned query generation, policy-aware search/frontier ingestion, robots/sitemap/RSS discovery, bounded related-link exploration, a versioned hackathon-like classifier, and an 80-case / 10-language / 76-domain golden set. The live open-web gate passed in GitHub Actions run 35884553285 with 4/4 policy decisions, 2 fail-closed hosts, 2 fetched hackathon-like pages, 2 related links and 2 sitemap candidates. The exact W3 persistence migration was also exercised on PostgreSQL 17 with query attribution, DISCOVERY_ONLY provenance, idempotent frontier upsert and false-positive review routing. W4 is fully closed with its API/search/frontend gates, and W5 Recommendation + Alerts is now fully closed with 6/6 issues / 42/42 points. PostgreSQL 17 verified preference/recommendation/saved-search/alert idempotency, GitHub Actions run 35905927315 passed Next.js production build and route smokes, and an SMTP message was delivered and inspected through Mailpit. The next implementation wave is W6 Admin + evals + release hardening.

## W6 continuation (2026-10-15)

Incremental W6 implementation: administrative role control (`VIEWER`/`OPERATOR`/`ADMIN`), transactional source pause/resume and review queue changes with audit trail, enforcement of persisted source pauses during PostgreSQL crawling, and versioned evaluation regression gates. See `docs/execution/W6_CONTINUATION_2026-10-15.md` for migration, access provisioning, commands, tests, known limitations and pending external acceptance gates. W6 is **not** production accepted.

### W6 — operações (iteração 2, 15/10/2026)

O painel de operação `/admin` (RBAC/CSRF, fontes, revisões, qualidade e auditoria) e o
controle de pausa transversal nos entrypoints de coleta estão descritos em
[`docs/execution/W6_ITERATION_2_2026-10-15.md`](docs/execution/W6_ITERATION_2_2026-10-15.md).
O checkpoint passou em **135 testes Python** e nos gates estáticos de avaliação,
mas a migração PostgreSQL em staging, o build Next.js e os testes de release externos
**não foram executados neste ambiente**. Não tratar a W6 como concluída.

### W6 — escalonamento/observabilidade de fontes (iteração 3, 15/10/2026)

Migração 0007, planejamento adaptativo persistente de recrawl, regressões idempotentes
encaminhadas à fila, leases para workers concorrentes, coleta periódica **opt-in** no
Celery Beat e dados de agenda no painel de operações. O sistema não dispara novas
coletas por padrão (`SOURCE_SCHEDULER_ENABLED=false`). A política PostgreSQL da
fonte tem precedência sobre o seed. No checkpoint da Iteração 3, **150 testes locais**
passaram, o baseline de evals passou e o verificador estático identificou 74 tabelas.
**Ainda não executados:** migração/integração em PostgreSQL real, build de frontend,
Celery+Redis em staging e gates de lançamento.

Instruções e validações pendentes: [`docs/execution/W6_ITERATION_3_2026-10-15.md`](docs/execution/W6_ITERATION_3_2026-10-15.md).

### W6 — DNS/IP pinning para egress HTTPS (iteração 6, 15/10/2026)

A coleta HTTP e a busca por `robots.txt` agora usam transporte com **resolução DNS
validada no instante da conexão** e socket direcionado ao IP público aprovado,
mantendo o hostname original na validação TLS/SNI/Host. Isso fecha o intervalo
TOCTOU entre a validação anterior e a nova resolução do cliente HTTP.
Os **186 testes locais** passaram, assim como os gates de avaliações e esquema
estático. Esses testes são simulados, não substituem verificação de firewall de
egress, DNS, rede real nem a execução integral de CI/staging. Veja
[`docs/execution/W6_ITERATION_6_2026-10-15.md`](docs/execution/W6_ITERATION_6_2026-10-15.md).
**W6 não está aprovada para produção.**

### W6 — concorrência de workers e relógio do banco (iteração 8, 15/10/2026)

As leases de agendamento usam `clock_timestamp()` do PostgreSQL para claim,
renovação e finalização, evitando divergências entre relógios dos workers.
O CI agora inclui um teste de disputa entre dois claimers PostgreSQL e um smoke
isolado da fila Celery/Redis. **218 testes locais** e o baseline de avaliações
passaram; os novos testes com serviços reais estão **configurados, mas não
executados neste ambiente**. Veja
[`docs/execution/W6_ITERATION_8_2026-10-15.md`](docs/execution/W6_ITERATION_8_2026-10-15.md).
O scheduler segue desligado por padrão e W6 não tem aceite de produção.

### W6 — proteção de entrada e readiness (iteração 9, 15/10/2026)

A API aplica `MAX_REQUEST_BODY_BYTES` aos **bytes recebidos** no middleware ASGI,
inclusive requisições fragmentadas e sem `Content-Length`; respostas 413 mantêm
CORS e headers de segurança. `GET /health` é liveness; `GET /ready` só retorna
200 se o PostgreSQL responder e o `alembic_version` corresponder à migração
`0007_w6_source_operations`. O CI contém probe de readiness opt-in após migrar
PostgreSQL; execução real continua pendente. **232 testes locais aprovados**, mais
baseline de evals e esquema estático. Detalhes:
[`docs/execution/W6_ITERATION_9_2026-10-15.md`](docs/execution/W6_ITERATION_9_2026-10-15.md).
**A W6 ainda não recebeu aceite de produção.**

### W6 — Iteração 10 (outbox de alertas)

As entregas de email vencidas agora podem ser despachadas por Celery Beat de forma **opt-in**,
com claims versionadas por token no PostgreSQL, retries e execução fenced. A migração
`0008_w6_alert_dispatch` é obrigatória; `/ready` exige essa revisão. A rotina permanece
**desabilitada por padrão** (`ALERT_DISPATCH_ENABLED=false`), não testa SMTP real e não
oferece garantia de exactly-once. O procedimento e a evidência de validação constam em
`docs/execution/W6_ITERATION_10_2026-10-15.md`.


### W6 — Iteração 11 (15/10/2026)

Produtor opt-in de alertas `new_match` para novas correspondências de buscas salvas, com deduplicação transacional, cursor PostgreSQL, consentimento explícito e revalidação na entrega de EMAIL e job de integração com SMTP simulado. A migração `0009_w6_alert_match` é exigida pelo endpoint `/ready`. O produtor e a outbox de SMTP continuam desabilitados por padrão. Consulte `docs/execution/W6_ITERATION_11_2026-10-15.md` para operação e limitações. A W6 **ainda não está pronta para produção**.

### Checkpoint W6 — Iteração 13 (2026-10-16)

A página de buscas salvas oferece configuração explícita dos canais `IN_APP` e
`EMAIL` para `new_match`; email requer opt-in e endereço no cadastro. A API
agora expõe `GET /v1/me/alert-preferences`, rejeita filtros de busca salva
inválidos com HTTP 422 e a caixa de alertas permite marcar mensagens como lidas.

A suíte local passou com **316 testes**, mas isso **não** libera produção: consulte
`docs/execution/W6_ITERATION_13_2026-10-16.md` para evidências e gates abertos
(build Next.js, PostgreSQL/Redis/Celery, SMTP de teste, DR e E2E externo).

### Checkpoint W6 — Iteração 14 (2026-10-19)

Transporte SMTP reforçado para autenticação somente sobre TLS validado, com
STARTTLS e TLS implícito, hostname/certificado verificados, bloqueio de
credenciais sem TLS e recusa de SMTP em claro quando a outbox é ativada em
`staging`/`production`. A suíte do checkout agora executa diretamente com
`pytest -q` graças aos caminhos locais corrigidos em `pyproject.toml`.
**338 testes locais aprovados**, sem alterações SQL. Testes reais de SMTP,
PostgreSQL/Celery, build Next.js e gates de release continuam pendentes.
Consulte [`docs/execution/W6_ITERATION_14_2026-10-19.md`](docs/execution/W6_ITERATION_14_2026-10-19.md).
