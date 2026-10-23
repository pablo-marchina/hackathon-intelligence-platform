# Coleta controlada de múltiplas fontes — operação da cobertura global

Entrega incremental do objetivo central da plataforma: **aumentar eventos
canônicos únicos por fonte autorizada e medir o resultado no PostgreSQL**.

## O que muda

O comando individual `scripts.ops.controlled_catalog_ingestion` permanece.
Esta camada o executa sequencialmente para um lote seguro de fontes, sem
paralelismo implícito, sem alteração de permissões e sem coleta em produção
não autorizada.

```bash
# Simular um lote com todas as fontes ALLOWED no registro.
# Não conecta ao PostgreSQL nem à internet.
python -m scripts.ops.controlled_batch_ingestion

# Seleção explícita e limites conservadores, também sem I/O.
python -m scripts.ops.controlled_batch_ingestion \
  --source hackclub_hackathons \
  --source hackclub_programs \
  --source agenda_tech_brasil \
  --max-pages 3 --max-versions 100

# Aplicação explícita no staging: exige aprovação adicional no PostgreSQL.
APP_ENV=staging \
HIP_ALLOW_LIVE_INGESTION=1 \
HIP_ALLOW_BATCH_INGESTION=1 \
DATABASE_URL="<staging database URL>" \
python -m scripts.ops.controlled_batch_ingestion \
  --max-pages 3 --max-versions 100 --apply
```

O relatório JSON indica a quantidade de fontes aceitas, bloqueadas e falhas,
as páginas lidas e registros de origem processados. Cada `crawl_run_id` tem
seus próprios resultados de `CREATED`, `LINKED`, `UPDATED`, `REJECTED`.
O relatório também compara o número total de hackathons ativos antes/depois.

## Semântica das métricas

- `run_attributed_outcomes.CREATED`: quantidade de registros de origem deste
  crawl que criaram canônicos; não representa necessariamente eventos
  recém-anunciados.
- `run_attributed_outcomes.LINKED`: referências vinculadas a canônicos já
  existentes; não adiciona eventos distintos.
- `catalog_delta_observed`: variação de hackathons únicos ativos durante a
  execução. Pode incluir concorrência e reprocessamento de dados antigos.
- `processed_this_invocation`: inclui versões mais antigas do backlog desta
  fonte, mesmo quando o crawl atual não as originou.
- Um relatório com todos os jobs bem-sucedidos não demonstra **cobertura
  global completa**, nem comprova que todos os links do registry coletam dados.

Para medir ganho real de cobertura, compare ainda o conjunto de eventos
únicos com amostras independentes, seu período temporal e os campos completos.

## Controles

- Apenas fontes `ALLOWED` com detalhes habilitados, seeds e aprovação da
  **base** podem coletar dados. Devpost e fontes em `REVIEW_REQUIRED` seguem
  proibidas até revisão explícita.
- São rejeitados nomes de fonte desconhecidos, duplicados, fontes
  não aprovadas e limites inválidos **antes** de abrir conexões.
- Máximo de 8 fontes por lote, 20 páginas e 1.000 versões por fonte,
  80 páginas e 4.000 versões por lote.
- `--apply` requer dois flags distintos, `APP_ENV` não-produtivo e
  `DATABASE_URL`. A invocação sem `--apply` nunca toca rede/banco.
- Uma fonte pausada ou sem permissão no PostgreSQL é marcada `BLOCKED`
  sem iniciar sua coleta. A condição é reavaliada por fonte.
- Uma falha numa fonte não impede que fontes independentes terminem.
  Erros da conexão são expostos apenas por tipo, sem mensagens/credenciais.

## Evidência de qualidade

```bash
pytest -q packages/source_sdk/tests/test_controlled_batch_ingestion.py
APP_ENV=test HIP_ALLOW_BATCH_DB_DRILL=1 \
  DATABASE_URL="<local disposable PostgreSQL>" \
  python -m scripts.ops.controlled_batch_integration_check
```

O segundo teste funciona apenas no PostgreSQL descartável da CI:
simula respostas de **duas APIs previamente aprovadas**, verifica que dois
registros de origem do mesmo evento geram exatamente **um** hackathon
canônico (um `CREATED` e um `LINKED`) e que o reprocessamento não
cria duplicatas. Todos os eventos de teste são sintéticos.

## Próximas prioridades

1. Executar um lote real no ambiente autorizado de staging e arquivar os
   recibos de cobertura por fonte e período.
2. Adicionar campos completos para elegibilidade, prêmios e inscrições com
   origem, confiança e histórico de mudanças.
3. Desenvolver conectores para mais plataformas **após** revisar e
   registrar permissões ou integrações oficiais.
4. Evoluir o painel de observabilidade de cobertura/completude e incorporar
   os filtros avançados dos PRs #5 e #8.
