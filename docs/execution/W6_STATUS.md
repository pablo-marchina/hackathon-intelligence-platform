# W6 — Admin, Evals & Release Hardening

## Implemented and locally verified

- deterministic event quality scoring;
- source/parser regression detection;
- adaptive recrawl priority/cadence planning;
- major-source failure isolation model;
- secure-by-default admin authorization dependency;
- admin read endpoints for review queue, source health, and quality snapshots;
- HTTP security headers, TrustedHost enforcement, and request body size guard;
- unified evaluation runner;
- official-event E2E fixture (NASA Space Apps Challenge 2026);
- PostgreSQL backup/restore scripts;
- query-plan inspection SQL;
- HTTP smoke-load tool with p95 gate.

## Runtime verification still required outside this execution environment

The current runner does not provide Docker/PostgreSQL/psql or outbound package installation. Therefore the following are implemented but must remain in Review until CI or a developer workstation runs them:

- admin endpoints against populated PostgreSQL;
- source health/data quality dashboards against real data;
- pg_dump/pg_restore recovery drill;
- EXPLAIN ANALYZE against production-like volume;
- HTTP load test against a running API + database;
- live source-failure drill;
- live previously-unknown-event acceptance test.

## Release rule

Do not mark the MVP release as complete until the CI migration round trip, database restore drill, production-like query plans, and live real-event end-to-end acceptance all pass.

## Continuação em 2026-10-15

Novas funcionalidades de W6: `admin_user_roles`/migração 0006, ações administrativas persistentes e auditadas, autenticação com RBAC/CSRF, pausa operacional consultada no runner persistente e avaliação versionada com thresholds no CI. Testes locais: 117/117. O runner REST e o modo sem persistência ainda não observam o switch PostgreSQL. Consulte `W6_CONTINUATION_2026-10-15.md` para escopo e gates externos.

## Iteração 2 (15/10/2026)

Painel `/admin` com leitura e mutações controladas por RBAC/CSRF e verificações de pausa
nos entrypoints CLI, REST e de diagnóstico; fail-closed sem estado PostgreSQL em ambientes
staging/production. **135 testes locais aprovados**. Detalhamento, instruções e gates
abertos: `W6_ITERATION_2_2026-10-15.md`.

## Iteração 3 (15/10/2026)

Implementadas migração `0007`, agenda de recrawl por fonte, regressoes automatizadas
para review queue, leases DB/fencing, Celery Beat opt-in e UI operacional de cadência.
**150 testes locais aprovados, evals PASS, verificação estática 74 tabelas / 5 extensões.**
A integração PostgreSQL fica prevista no CI e permanece **não executada localmente**.
O gate de W6/produção segue aberto; detalhes em `W6_ITERATION_3_2026-10-15.md`.

## Iteração 4 (15/10/2026)

Corrigido o caminho SSRF de `robots.txt` (redirects verificados), limites de corpo HTTP
para fonte/robots e renovação do token de lease em transação própria com interrupção
fail-closed na perda de autorização. **169 testes locais passaram; evals PASS.**
Ver `W6_ITERATION_4_2026-10-15.md`. Os testes de infraestrutura, lint/typecheck
externos e aceite real de segurança ainda são pendências obrigatórias.

## Iteração 5 (15/10/2026)

Implementado fence transacional `FOR SHARE` no commit de páginas e de tentativas HTTP,
verificação de lease via `clock_timestamp()` e recusa da finalização com lease vencida.
Um drill de concorrência com PostgreSQL real foi preparado para CI (não executado localmente).
**179 testes locais aprovados e avaliações PASS.** Consulte
`W6_ITERATION_5_2026-10-15.md`. Os gates de produção seguem abertos.

## Iteração 6 (15/10/2026)

O transporte de coleta e `robots.txt` passou a usar DNS pinning **no socket**, com
resolução integralmente validada, conexão ao IP literal público, TLS/SNI/Host
originais e recusa de proxy/socket Unix. **186 testes locais aprovados; evals
PASS; verificação estática 74 tabelas / 5 extensões.** Isso não substitui
testes com rede, DNS e egress firewall reais, nem os demais gates de W6.
Consulte `W6_ITERATION_6_2026-10-15.md`. Scheduler segue desativado por padrão.

## Iteração 7 (15/10/2026)

Recuperação de desastres endurecida: `pg_restore` agora executa em banco de
escopo explícito (não apenas imprime SQL), conexão SQLAlchemy é convertida para
libpq sem expor credenciais em argumentos, MinIO recebe as credenciais do
projeto, e o manifesto valida duplicação, tamanho e caminhos/symlinks antes
da escrita. Adicionado job CI com PostgreSQL/MinIO efêmeros e testes de
restauração/comparação. **205 testes locais aprovados.** O exercício real foi
**preparado, mas não executado** no ambiente local; os gates externos continuam
abertos. Ver `W6_ITERATION_7_2026-10-15.md`.

## Iteração 14 (19/10/2026)

Autenticação SMTP exige TLS com verificação de certificado/hostname;
STARTTLS e SMTP_SSL são mutuamente exclusivos. Outbox ativa em
staging/production não pode usar SMTP em claro, inclusive sem autenticação.
`pytest -q` passa sem instalar o projeto (338 testes). Evals e esquema passam
localmente, mas as provas com serviços reais e o build Next.js permanecem
pendentes. Consulte `W6_ITERATION_14_2026-10-19.md`.

## Iteração 15 (19/10/2026)

Consentimento EMAIL **negado por padrão para todos os tipos de alerta**, com
precedência de regra específica sobre wildcard; destinatário SMTP relido sob
trava de usuário antes do envio e regra centralizada entre API, produtor e worker.
Drill PostgreSQL + SMTP simulado ampliado no CI para negação, atualização de
email e concorrência. **351 testes locais aprovados; evals PASS; 75 tabelas/5
extensões**. Integrações reais e build do frontend seguem pendentes, portanto
W6 e release **não aprovados**. Consulte `W6_ITERATION_15_2026-10-19.md`.
