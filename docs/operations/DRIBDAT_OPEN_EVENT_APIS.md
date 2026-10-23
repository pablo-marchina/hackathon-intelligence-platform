# Integrações Dribdat: Swiss GLAMhack e IMPRS Astro Hackathon

## Fontes verificadas

O [Dribdat](https://dribdat.cc/contribute) fornece APIs publicamente
documentadas para eventos, incluindo `GET /api/events.json`, que devolve
eventos públicos (não ocultos, não bloqueados), sem dados de participantes
nem chaves de API. As próprias instalações incluem esse endpoint em seus
painéis **About → Open data**.

Adicionamos **somente duas instalações verificadas diretamente**:

| Chave | API pública | Tipo de evidência |
| --- | --- | --- |
| `dribdat_glamhack` | `https://hack.glam.opendata.ch/api/events.json` | GLAMhack, Swiss Open Cultural Data Hackathon, com edições 2015–2026 |
| `dribdat_imprs_astro` | `https://live.imprs-astro-hackathon.de/api/events.json` | IMPRS Astro Hackathon 2024, Alemanha |

São feeds *dos organizadores ou suas próprias instalações*, e não cópias
de listagens originadas de Devpost ou outros serviços restritos.

Ambos têm `permission_status=ALLOWED` no registry por exporem a API aberta,
mas a autorização operacional em PostgreSQL é independente e continua
obrigatória. Os seeds ficam limitados a um host por fonte, sem links seguintes
do organizador, sem descoberta automática de outras instalações, com
no máximo duas requisições por minuto, concorrência 1 e intervalos de refresh
de 12/24 horas.

## Contrato factual

O adaptador reutilizável `DribdatOpenEventsAdapter` processa
`{"events":[...]}` e captura somente dados publicados:

- `id` (identidade **por instalação**, não ID global), `name`, datas
  `starts_at` / `ends_at` RFC 2822 com timezone explícito;
- `webpage_url` válido somente como URL oficial, **não** como permissão
  de crawling;
- `summary` como `short_description`, preservada com referência à
  observação e à seleção canônica;
- `hostname`, `location`, `hashtags`, `logo_url` e `has_finished`
  permanecem nos metadados da observação bruta, sem inventar cidade,
  país, prêmio, custo ou prazo de inscrição.

Para títulos explícitos como `GLAMhack`, `Open Cultural Data Hackathon`
e `IMPRS-Astro Hackathon`, o classificador identifica hackathon. O
classificador reconhece também `hackdays` na taxonomia geral, mas nunca
autoriza automaticamente qualquer evento de um servidor Dribdat apenas
por ele usar esse software: workshops e atividades sem classificação
positiva são rejeitados no normalizador.

Se um feed tiver mais de 2.000 eventos, a execução falha em vez de
truncar silenciosamente a cobertura. IDs inválidos, registros sem nome,
datas ambíguas e URLs com credenciais não são aceitos. Dados ausentes
permanecem `unknown`.

## Operação e validação

Dry-run sem banco ou rede:

```bash
python -m scripts.ops.controlled_catalog_ingestion --source dribdat_glamhack
python -m scripts.ops.controlled_catalog_ingestion --source dribdat_imprs_astro
```

Campanha de staging autorizada (após conferir a aprovação operacional no DB):

```bash
APP_ENV=staging HIP_ALLOW_LIVE_INGESTION=1 \
HIP_ALLOW_BATCH_INGESTION=1 HIP_ALLOW_COVERAGE_CAMPAIGN=1 \
DATABASE_URL="<staging DSN>" \
python -m scripts.ops.coverage_campaign \
  --source dribdat_glamhack --source dribdat_imprs_astro \
  --max-pages 1 --max-versions 200 --apply
```

Testes de CI **sem HTTP real**, em PostgreSQL local descartável:

```bash
pytest -q packages/source_sdk/tests/test_dribdat_public_api.py
APP_ENV=test HIP_ALLOW_DRIBDAT_DB_DRILL=1 \
DATABASE_URL="<local disposable PostgreSQL>" \
python -m scripts.ops.dribdat_open_api_integration_check
```

O teste end-to-end verifica edições distintas e históricas, um workshop
rejeitado, descrição selecionada com origem, URLs externas não acessadas e
reexecução idempotente.

## Limitações

- Não há execução real autorizada em staging neste PR: os cenários SQL usam
  páginas sintéticas baseadas no formato real observado.
- Os dados podem ser históricos, cancelados, alterados ou ter pouca informação
  sobre registro/eligibilidade. Uma edição de 2015 **não** é evento futuro.
- `DONE` do backlog só significa que os seeds cadastrados foram buscados;
  **não** demonstra cobertura global.
- Outras instalações Dribdat precisam ser verificadas individualmente antes
  de qualquer novo registro/ativação.
