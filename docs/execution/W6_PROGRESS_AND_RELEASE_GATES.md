# W6 — progresso verificável (checkpoint)

Data: 2026-10-15. Escopo: Admin + Evals + Release Hardening (10 issues / 63 pts).

## Implementado neste checkpoint

1. **Logs com redação centralizada**: `packages/shared/src/hackathon_shared/logging.py` oculta passwords, Bearer tokens, URLs de PostgreSQL com senha, tokens em query strings e secrets de ambiente, incluindo exceções e metadados estruturados.
2. **Backup PostgreSQL + S3/MinIO**: `scripts/ops/disaster_recovery.py` faz `pg_dump` custom e cópia de objetos com manifesto (`sha256` por objeto/dump). `validate` verifica TODOS os checksums antes de qualquer restore. `restore` exige target explícito e `--confirm-restore`; evita passar URL de banco na linha de comando (usa `PGDATABASE` na env do subprocesso). Nomes de arquivo derivam de hash da chave do objeto.
3. **SSRF/redirect safety**: `network_guard.py` bloqueia destinos não HTTPS, userinfo/portas não padrão, hosts não autorizados, IPs não globais e respostas DNS com IPs privados/reservados; o fetcher desabilita follow_redirects automático e valida cada redirecionamento, com fail-closed.
4. **Testes**: 99/99 pytest PASS; verifier 71 tabelas e 5 extensões; unified eval PASS.

## Gates que continuam abertos — NÃO marcar W6 como concluída

- DR real em PostgreSQL + MinIO com `backup → destruir ambiente de teste → restore → FKs/objetos comparados`, RPO/RTO mensurados e storage/credentials separados.
- Proteção SSRF reforçada em infraestrutura: pinning de DNS/IP entre validação e conexão, além de egress firewall (proteção de DNS rebinding/TOCTOU). Testes de redirect com servidor real e testes de abuso/rate limiting.
- Control plane admin: ações persistidas + auditadas, pause/resume, dashboards e queues, bem como testes de RBAC.
- Evals versionados, baselines e thresholds obrigatórios em CI, SAST/dependency scan.
- Load test da API (não só SQL), slow-query plans, source-failure drill em staging e E2E de evento descoberto até alerta/UI.

## Como executar o DR

```bash
# Necessita pg_dump/pg_restore, boto3, acesso ao PostgreSQL e ao bucket S3/MinIO.
export DATABASE_URL='postgresql://...'
export S3_BUCKET='hackathon-raw'
export S3_ENDPOINT_URL='http://minio:9000'  # somente rede privada de teste
python scripts/ops/disaster_recovery.py backup /backups/hip-20261015
python scripts/ops/disaster_recovery.py validate /backups/hip-20261015
# IMPORTANTE: apontar para um DB e bucket efêmeros de RESTORE, nunca produção
python scripts/ops/disaster_recovery.py restore /backups/hip-20261015 \
  --target-db 'postgresql://...test-restore...' --target-bucket hip-restore-test --confirm-restore
```

**Limites do teste**: mock/manifest tests NÃO provam que `pg_restore` e `boto3` funcionam com serviços reais. O script também não apaga objetos extras já existentes no bucket de destino; para um drill correto, use bucket vazio. Não usar este comando para substituir production backup sem política de retenção, criptografia e verificações automáticas de acesso.

## Incremento de 2026-10-15 (sem alterar gates externos)

O control plane e o baseline das avaliações avançaram neste checkpoint: migração 0006, auditoria e RBAC das mutações, pausa consultada pelo runner persistente e gate versionado no CI. Ver `W6_CONTINUATION_2026-10-15.md`. O resultado de 117 testes locais não substitui um teste integrador em PostgreSQL/CI; W6 continua aberta.
