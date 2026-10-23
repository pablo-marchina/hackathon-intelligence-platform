# Campanhas de cobertura: medição antes/depois no staging (v1)

## Finalidade

Os coletores existentes já executam fontes autorizadas em lote, mas uma única
contagem de `CREATED` não demonstra o crescimento do catálogo global. Edições
podem ser vinculadas entre fontes, backfills históricos podem aparecer como
novos registros, e merges/edições concorrentes alteram as contagens líquidas.

A CLI `scripts.ops.coverage_campaign` associa **duas fotografias SQL
consistentes** ao mesmo relatório de coleta controlada para documentar essas
diferenças. Nenhuma métrica afirma cobrir todos os hackathons do mundo.

## Primeiro passo: planejamento sem I/O

```bash
python -m scripts.ops.coverage_campaign \
  --source hackclub_hackathons --source hackclub_programs \
  --max-pages 3 --max-versions 100
```

Padrão `PLAN_ONLY`. Não acessa PostgreSQL, não cria cliente HTTP, não
autoriza fonte, não usa credenciais nem executa cron.

## Execução real exclusivamente autorizada no staging

Configure um PostgreSQL **migrado** e dedicado ao staging, usando o cadastro
de fontes com permissões liberadas no próprio banco. Cada flag é necessária:

```bash
APP_ENV=staging \
HIP_ALLOW_LIVE_INGESTION=1 \
HIP_ALLOW_BATCH_INGESTION=1 \
HIP_ALLOW_COVERAGE_CAMPAIGN=1 \
DATABASE_URL="<staging PostgreSQL DSN>" \
python -m scripts.ops.coverage_campaign \
  --source hackclub_hackathons \
  --source hackclub_programs \
  --max-pages 3 --max-versions 100 \
  --min-net-unique 1 --require-drained --apply \
  > coverage-campaign-staging.json
```

`--min-net-unique` é o **limiar observado de variação líquida** de
`hackathons.id` ativos, não a produção causal de eventos novos. Essa
opção e `--require-drained` definem gates opcionais para o código
de saída (falha quando o alvo não é atingido, a coleta não foi bem
sucedida ou restou backlog pendente/exaurido conhecido).

Outras políticas existentes continuam vigentes: apenas fontes `ALLOWED`
no registry e no banco, limitação de fontes/páginas/versões, pausa,
rate limiting, leases, autorização de domínio e coleta de histórico.
Esta CLI restringe ainda mais os ambientes de execução a `staging` e
`test`: não suporta nem `production` nem `development`.

## Estrutura do recibo JSON

- `schema_version=coverage-campaign-v1`: versão de esquema do recibo.
- `evidence_kind=OBSERVED_DATABASE_BEFORE_AFTER_NOT_CAUSAL`: **observação**,
  não causalidade nem medição de recall global.
- `database_as_of.before/after`: instantes de cada transação SQL.
- `snapshots.before/after.catalog`: canônicos globais distintos, inscrições
  abertas somente em janela confirmada, datas e outras contagens.
- `snapshots.before/after.field_counts`: campos conhecidos no canônico
  global; desconhecido não equivale a falso ou não aplicável.
- `snapshots.before/after.pipeline`, `global_funnel`: backlog de
  normalização, referências e eventos de fato pesquisáveis.
- `per_source.<key>.net_deltas.attribution`: variação de canônicos
  atribuídos, exclusivos do banco, compartilhados e campos incompletos
  **desta fonte**. Essas contagens se sobrepõem entre fontes, NÃO se somam.
- `per_source.<key>.net_deltas.funnel`: variação das referências e dos
  canônicos distintos com evidências aprovadas.
- `batch`: relatórios por fonte, `crawl_run_id`, estados de fetch,
  `CREATED/LINKED/UPDATED/REJECTED`, backlog, páginas e limites.
- `has_incomplete_or_exhausted_backlog`: sinaliza páginas pendentes
  por orçamento ou esgotadas; nenhuma fonte não rastreada é inferida
  como completamente coletada.

Fotografias são feitas em transações PostgreSQL `REPEATABLE READ READ
ONLY`: consultas diferentes dentro da mesma fotografia observam um
mesmo estado consistente. As duas fotografias são separadas pela execução
da coleta e podem refletir escritores **concorrentes**. Mesmo que a
variação global seja +1, isso NÃO prova que a campanha causou essa criação.

Uma fonte recém cadastrada, ainda ausente da seleção de métricas
(`limit=100` do painel administrativo), é exibida com
`attribution=null` — nunca com zero inventado.

## Teste ponta a ponta no CI

O teste de integração real do batch agora usa a campanha sobre PostgreSQL
descartável e HTTP inteiramente sintético. Verifica:

1. Duas APIs oficiais aprovadas publicando **um mesmo hackathon**.
2. `CREATED=1`, `LINKED=1`; crescimento líquido global **+1**.
3. Atribuição **+1 por fonte**, sem somar `+2` como +2 globais.
4. Aumento **+1** dos eventos únicos de fato pesquisáveis.
5. Reexecução das fontes sem HTTP adicional ou duplicatas e crescimento **0**.
6. Limitações e gates de observabilidade permanecem explícitos.

```bash
pytest -q packages/source_sdk/tests/test_coverage_campaign.py
APP_ENV=test HIP_ALLOW_BATCH_DB_DRILL=1 \
DATABASE_URL="<disposable local PostgreSQL>" \
python -m scripts.ops.controlled_batch_integration_check
```

Não há coletas externas nem acesso a contas/credenciais de produção pelo CI.

## Próximos passos de entrega

1. Executar a campanha em **staging autorizado** e arquivar o recibo real.
2. Investigar `RETRY`, `EXHAUSTED` e cobertura incompleta por edição
   antes de ampliar o limite de páginas.
3. Selecionar novos conectores *com integração oficial ou permissão revisada*,
   comparando ganho líquido, atribuição exclusiva e qualidade dos dados.
4. Após demonstrar ganho real, instrumentar SLOs e processo de revisão
   antes do lançamento à produção.

Nenhum ganho mundial real é alegado até o primeiro recibo válido de staging.
