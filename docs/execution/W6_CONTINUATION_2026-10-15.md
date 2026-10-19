# W6 — entrega incremental: control plane administrativo e gates de avaliação

Data: 2026-10-15. Esta entrega continua o ZIP de checkpoint W6 sem substituir nem reiniciar W0–W5.

## Entregue (código e testes locais)

- Migração `0006_w6_admin_control_plane` adiciona `admin_user_roles` com funções `VIEWER`, `OPERATOR`, `ADMIN` e expande o `admin_audit_log` com `actor_kind` e `request_id`.
- Autorização administrativa: chave de serviço (`X-Admin-Key`) ou sessão autenticada com papel no banco; a chave funciona como operador de sistema com poder de `ADMIN`. Ações por sessão exigem CSRF válido; não há papel de administrador inferido por email ou provedor OAuth.
- APIs: `GET /admin/audit` (VIEWER+), `PATCH /admin/sources/{id}/active` (OPERATOR+), `PATCH /admin/review-queue/{id}` (OPERATOR+), `POST /admin/search/reindex` (ADMIN). Todas as mutações exigem motivo explícito e auditoria no mesmo `Connection.begin()` da mudança. Fonte inexistente gera 404, transição inválida de fila gera 409, operação idempotente não duplica auditoria.
- A execução persistente de fontes consulta `sources.active` antes de criar `crawl_runs` e entre páginas. Uma fonte pausada é ignorada (`SKIPPED_PAUSED` na CLI); pausas no meio da execução encerram a corrida com `CANCELLED`. `ON CONFLICT` na sincronização dos metadados da fonte não reativa a fonte.
- Baseline `evals/gates/w6_baseline_v1.json` com métricas, tolerância de regressão, tamanho mínimo do dataset e limites de suporte. `scripts/check_eval_gates.py` executa o avaliador unificado e falha se métricas faltarem, duplicarem, retrocederem, se amostras forem reduzidas ou se um falso auto-merge for registrado.
- CI ampliado com gate de avaliações, teste integrador da API administrativa no PostgreSQL migrado e varredura de dependências (`pip-audit --strict`). Cache npm desabilitado por não haver `package-lock.json` no checkpoint original.

## Como aplicar a migração

```bash
pip install -e '.[dev]'
alembic -c db/alembic.ini upgrade head
```

**Provisão de acesso:** um DBA autoriza explicitamente o UUID de um usuário autenticado. Não executar com uma conta que não foi validada administrativamente.

```sql
INSERT INTO admin_user_roles(user_id, role)
VALUES ('UUID_DO_USUARIO', 'OPERATOR')
ON CONFLICT(user_id) DO UPDATE SET role = EXCLUDED.role;
```

`VIEWER` só lê; `OPERATOR` lê, altera o estado de fontes e trata revisão; `ADMIN` também pode reindexar. Sessões precisam de `hip_session`, `hip_csrf` e cabeçalho `X-CSRF-Token` igual ao cookie para mutações. A chave de sistema `ADMIN_API_KEY` deve existir apenas no backend/secret manager, **nunca embutida no JavaScript do browser**.

Exemplo com chave de serviço (ambiente confiável):

```bash
curl --fail-with-body -X PATCH "http://localhost:8000/admin/sources/UUID_DA_FONTE/active" \
  -H "X-Admin-Key: $ADMIN_API_KEY" -H 'Content-Type: application/json' \
  --data '{"active":false,"reason":"Pausa operacional para revisar a fonte"}'

curl --fail-with-body "http://localhost:8000/admin/audit" \
  -H "X-Admin-Key: $ADMIN_API_KEY"
```

Ao reiniciar o worker com `DATABASE_URL` e persistência PostgreSQL, a fonte permanece pausada mesmo depois da sincronização do registro.

## Verificação local (15/10/2026)

```bash
# Após instalar o projeto: pip install -e '.[dev]'
pytest -q
python scripts/verify_repository.py
python scripts/check_eval_gates.py
```

Resultados: **117/117 testes passam**, **72 tabelas SQL identificadas estaticamente**, 5 extensões, baseline W6 `PASS`. Os testes adicionais cobrem respostas HTTP (401/403/404/409/422), autorização por função, CSRF, mudança/auditoria de fonte, idempotência, fila, preservação de fonte pausada e validação negativa dos gates.

## Validação externa obrigatória (ainda aberta)

Este ambiente **não tem PostgreSQL/Docker/psql, credenciais de staging, acesso à rede para instalar ferramentas, nem prova de pipeline GitHub executado**. Portanto:

1. Rodar migração/downgrade/upgrade com PostgreSQL real e executar `HIP_ALLOW_ADMIN_DB_DRILL=1 APP_ENV=test DATABASE_URL=... python scripts/ops/admin_integration_check.py` **somente em banco efêmero isolado**. O script cria dados transitórios e os remove.
2. Executar GitHub Actions CI (inclui `ruff`, `mypy`, Next lint/build e `pip-audit`; ainda sem confirmação neste ambiente). Verificar possíveis falhas preexistentes de format/lint, pois os arquivos originais não foram normalizados em massa.
3. Implementar propagação de pause/resume a **todos** os caminhos de coleta: nesta entrega apenas `run_source_persisted` no PostgreSQL respeita o `sources.active`; `--no-persist` e o gateway REST/Supabase **ainda não compartilham esse controle**. Sem isso, não declarar pause global.
4. Confirmar DR real PostgreSQL+MinIO, DNS pinning/egress firewall, testes de redirect com servidor real, rate limiting, SAST, p95 da API com volume real, isolamento de fontes em staging e E2E de evento novo até alerta/UI.
5. Dashboard frontend administrativo, anomalias de saúde das fontes e integração de recrawl adaptativo com o escalonamento continuam pendentes.

**Status: W6 em desenvolvimento, não concluída; nenhum gate de produção foi artificialmente marcado como aprovado.**
