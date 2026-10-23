# Paginação durável das APIs oficiais aprovadas

## Problema de cobertura corrigido

Anteriormente, as integrações com `ingestion_mode=API` usavam uma lista de
URLs **somente em memória**. Se a API devolvesse a próxima página e o limite
`--max-pages` fosse atingido, essa URL desapareceria com a execução.
A próxima invocação começava de novo nos seeds, podendo nunca atingir as
páginas mais profundas e ocultando uma parcela de hackathons publicados.

## Nova operação

A opção `durable_api_pages` pertence ao cadastro curado da fonte e tem valor
**false** por padrão. Foi explicitamente habilitada apenas nas quatro APIs
já registradas como `ALLOWED`:

- `hackclub_hackathons`
- `hackclub_programs`
- `hackclub_community_events`
- `agenda_tech_brasil`

Agora o coletor reutiliza `source_url_backlog` também para essas APIs:

1. Registra URLs iniciais validadas (SEED) no PostgreSQL e mantém a janela
   de atualização; não recomeça sempre na primeira URL.
2. Faz o claim transacional de uma página usando token e lease persistente.
3. Extrai apenas links com `discovery_method=api_pagination` e hostname
   autorizado; **não** segue websites externos dos organizadores.
4. Guarda páginas DISCOVERED com `pagination_depth` e teto de **50 saltos
   entre execuções** para impedir listas infinitas.
5. Faz commit atômico de evidência HTTP, referências, URLs seguintes e
   confirmação do claim. Se falhar ou perder o lease, segue a política de
   retry e nunca descarta silenciosamente a página pendente.
6. Preserva todas as políticas anteriores: somente fontes autorizadas pelo
   cadastro **e** pelo PostgreSQL, pausa do operador, limites de rede e
   monitoramento de crawl.

As APIs que não publicam paginação continuarão limitadas àquilo que seu
endpoint efetivamente expõe. Para a API de programas Hack Club há um limite
documentado de até 100 registros por status, sem offset/cursor anunciado;
este patch **não promete** cobertura além desse teto.

## Observabilidade: um job SUCCEEDED não equivale a cobertura completa

Os relatórios de `controlled_catalog_ingestion.py` e de
`controlled_batch_ingestion.py` agora incluem:

- `backlog_tracked`: indica se há controle persistido de páginas;
- `backlog_by_state`: estados `PENDING`, `DONE`, `RETRY`, `CLAIMED`,
  `EXHAUSTED` conforme existirem;
- `unprocessed_backlog_urls`: páginas descobertas ainda não concluídas
  (`PENDING + RETRY + CLAIMED`);
- `backlog_exhausted_urls`: páginas que esgotaram tentativas;
- `budget_hit_with_pending_urls`: **true** se a execução consumiu o limite
  de páginas e ainda existem URLs que precisam de processamento;
- `sources_budget_limited`: quantidade de fontes com esse alerta no lote.

Quando a fonte não usa backlog, a quantidade de páginas restantes é
**null/desconhecida**, não falsamente zero. `DONE` também não prova que a
fonte mundial está completamente representada.

## Como verificar

```bash
# Planejamento sem HTTP nem PostgreSQL
python -m scripts.ops.controlled_batch_ingestion \
  --source hackclub_hackathons --max-pages 1

# Staging, apenas com autorização explícita do operador no banco e flags:
APP_ENV=staging HIP_ALLOW_LIVE_INGESTION=1 \
HIP_ALLOW_BATCH_INGESTION=1 DATABASE_URL="<staging DB>" \
python -m scripts.ops.controlled_batch_ingestion \
  --source hackclub_hackathons --max-pages 1 --apply
```

O teste obrigatório de CI:

```bash
APP_ENV=test HIP_ALLOW_API_PAGINATION_DRILL=1 \
DATABASE_URL="<disposable local PostgreSQL>" \
python -m scripts.ops.official_api_pagination_integration_check
```

Usa dados **sintéticos** e URLs de fixture fechadas, nunca rede externa.
Força 3 páginas com limite **1 por execução**, verifica progressão sem
recomeçar no seed, manutenção de 3 referências, impedimento após pausa no
PostgreSQL, ausência de refetch prematuro e bloqueio da página 51 quando uma
URL pai tem profundidade 50.

## Limitações remanescentes

- O orçamento e o refresh configurado regulam quando páginas são lidas.
  Ainda é necessário rodar as coletas efetivas em **staging autorizado**.
- Recolher todas as URLs de uma API não prova que o catálogo contenha
  todos os hackathons do planeta.
- Sem novos sites automaticamente permitidos. Devpost e outras plataformas
  com status `REVIEW_REQUIRED`/`RESTRICTED` permanecem desabilitadas.
- Métricas reais de crescimento somente a partir das execuções autorizadas,
  reconciliando edições e fontes diferentes.
