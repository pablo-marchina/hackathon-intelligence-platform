# Execução de campanhas reais via GitHub Actions — staging

## Resultado e limite de segurança

O workflow manual `.github/workflows/staging-coverage.yml` usa o
`scripts.ops.staging_campaign_dispatch` para executar uma campanha de
cobertura real **somente após autorização explícita em staging**.
Não existe `schedule`, trigger de push ou pull request. Código de
PR nunca recebe a credencial do ambiente de staging.

**Nada é executado automaticamente após o merge.** Por padrão, o workflow
somente valida um perfil de fontes autorizado no registry e produz um plano
sem abrir conexão com o banco, usar segredos ou acessar websites.

Os relatórios são **mudanças observadas**, não provas de causalidade,
recall da internet ou completude mundial.

## Preparação de responsabilidade do operador

1. Crie um banco PostgreSQL **dedicado e acessível pelo runner GitHub Actions**,
   com nome contendo `staging`, por exemplo `hip_staging`.
   Nunca reutilize um DSN de produção. Configure TLS, allowlist de
   endereços e uma conta com privilégios mínimos de leitura/escrita nas
   tabelas estritamente necessárias; o job não executa migrations.
2. Migre **separadamente**, com revisão operacional, até o head exato
   da branch `main`: `alembic -c db/alembic.ini upgrade head`.
   A campanha recusa schemas atrasados e não tenta consertá-los.
3. Insira/atualize **explicitamente** os cadastros de fonte nesse banco.
   A mera presença em `db/seeds/sources_v0_1.json` não ativa uma fonte.
   A fonte precisa estar `active=true`, com descoberta, extração e
   `permission_status='ALLOWED'` no PostgreSQL. `REVIEW_REQUIRED`
   e `RESTRICTED` permanecem bloqueadas.
4. Em **GitHub → Settings → Environments**, crie o ambiente
   `staging-coverage`, restrinja a implantação à branch `main`
   e habilite *required reviewers* (quando disponível no plano).
   Não configure o DSN como secret geral do repositório.
5. No **ambiente protegido**, configure:
   - Secret: `STAGING_DATABASE_URL` — DSN PostgreSQL real com banco staging.
   - Variable: `STAGING_DATABASE_NAME` — exatamente o nome, por exemplo
     `hip_staging`.
6. Confirme que o runner consegue acessar o banco com TLS; caso seja uma
   rede privada, configure um runner confiável com acesso ao staging.
   O workflow não libera firewall, não executa migrations e não cadastra
   ou aprova fontes automaticamente.

**Defesas independentes:** revisão manual do ambiente, confirmação textual,
branch `main`, opt-ins de ingestão, limites de páginas/versões, validação
do nome do banco no DSN e pelo PostgreSQL, revisão Alembic e checagem
de TODAS as fontes **antes da primeira requisição HTTP**. O coletor
continua verificando revogações e pauses no decorrer da execução.

## Executar pelo GitHub

1. Abra **Actions → staging-coverage-campaign → Run workflow**.
2. Selecione a branch `main`, modo `plan`, perfil `dribdat`,
   `max_pages=1`, `max_versions=25`. O perfil é fixo; nenhum nome
   livre de fonte ou comando shell é aceito.
3. Consulte o artefato `hip-staging-coverage-plan` (14 dias). Nenhuma
   rede externa/banco é acessado.
4. Após validar a configuração de staging, execute novamente em modo
   `apply`, digitando exatamente `RUN_STAGING_COVERAGE` em `confirm`.
   Aprove o ambiente se houver revisão exigida.
5. Consulte o artefato `hip-staging-coverage-receipt`, inclusive em caso
   de erro (se o job tiver iniciado). Apenas métricas agregadas estão
   presentes no JSON, não DSN, cabeçalhos, HTML ou documentos de terceiros.

Os perfis permitem rodadas pequenas e isoladas:

| Perfil | Fontes no registry |
| --- | --- |
| `dribdat` | `dribdat_glamhack`, `dribdat_imprs_astro`, `dribdat_opendata_ch` |
| `dribdat_community` | `dribdat_hack4socialgood`, `dribdat_echtzeit` (fontes oficiais Dribdat; validar separadamente) |
| `hackalendar` | `hackalendar` (API pública com paginação; validar separadamente) |
| `hackclub` | `hackclub_hackathons`, `hackclub_programs`, `hackclub_community_events`, `hackclub_ysws_programs`, `agenda_tech_brasil` |
| `all` | Todas as 8 fontes acima |

O limite é **1, 3 ou 5 páginas por fonte**, com **25, 100 ou 200
versões por fonte**. A campanha conserva também os tetos do batch existente.

## Critérios de aceitação e interpretação

O recibo v1 publica somente `status`, perfil, orçamento,
quantidade de fontes verificadas, métricas agregadas por fonte,
`catalog_net_deltas`, `field_net_deltas`, estado conhecido da fila,
alvo e limitações. Não contém segredos nem observações individuais.

- `MEASURED`: ocorreu uma comparação de antes/depois que atingiu o alvo,
  sem backlog conhecido pendente/não verificável.
- `BELOW_TARGET`: resultado menor que `min_net_unique` (0, 1 ou 5).
- `NEEDS_BACKLOG_FOLLOWUP`: páginas pendentes/esgotadas ou rastreamento
  desconhecido. Por padrão a execução pode terminar com exit 0
  **com ressalvas**; marque `require_drained` para torná-lo bloqueador.
- `BLOCKED`: confirmação ausente, banco incorreto, schema desatualizado
  ou qualquer fonte não aprovada; **coleta não iniciada**.
- `FAILED`: erro de execução. O relatório retém apenas o tipo da exceção,
  sem mensagem detalhada que possa conter segredo.

`catalog_net_deltas.unique_active` é a variação de hackathons canônicos
ativos naquele banco entre duas fotografias consistentes. Eventos podem
estar presentes em mais de uma fonte; **não some atribuições** como se
fossem eventos únicos. Concorrência, merges e backfill também afetam os
valores, de modo que não há alegação de ganho causal ou cobertura global.

O runner tem timeout de 20 minutos, permissões `contents: read`,
`cancel-in-progress: false` e bloqueio de sobreposição de campanhas
na mesma concorrência de workflow. A retenção do recibo é de 14 dias.
Não há mecanismo para rodar o workflow em branches de PR.

## Testes obrigatórios em CI

O CI padrão mantém **HTTP sintético** e PostgreSQL descartável;
não possui o segredo de staging.

```bash
pytest -q packages/source_sdk/tests/test_staging_campaign_dispatch.py

APP_ENV=test HIP_ALLOW_STAGING_PREFLIGHT_DRILL=1 \
DATABASE_URL="<disposable localhost PostgreSQL>" \
python -m scripts.ops.staging_campaign_preflight_integration_check
```

O drill PostgreSQL valida schema Alembic atual, banco correto,
duas fontes simultaneamente autorizadas, revogação administrativa de
uma das fontes e pausa `active=false`, sem emitir requisições.

## Critérios restantes para lançamento

Com o banco de staging provisionado e o workflow aprovado, executar
primeiro o perfil `dribdat`, revisar o recibo e eventuais páginas
pendentes, então expandir a `hackclub` e `all`. Usar os dados
reais para definir SLO de atualização, critérios mínimos de campos
conhecidos e expansão de fontes autorizadas. Um CI verde e um
`MEASURED` não equivalem a produção lançada.
