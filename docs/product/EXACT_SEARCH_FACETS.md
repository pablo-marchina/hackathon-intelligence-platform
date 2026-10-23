# Facetas exatas da busca avançada — v1

## O que foi implementado

A rota pública **POST `/v1/hackathons/facets`** aceita os mesmos filtros de
`POST /v1/hackathons/query`, incluindo filtros clássicos, expressões
AND/OR/NOT aninhadas, valores tipados e atributos curados com proveniência.

Retorna contagens exatas no PostgreSQL **sobre todas as edições canônicas
ativas que combinam com a consulta**, não apenas os registros da página
atual. O WHERE é compilado pelo mesmo builder da busca paginada,
mas não inclui ordenação, cursor nem LIMIT de resultados.

Exemplo:

```http
POST /v1/hackathons/facets
Content-Type: application/json
```

```json
{
  "expression": {
    "and": [
      {"field": "format", "op": "eq", "value": "ONLINE"},
      {
        "or": [
          {"field": "country_code", "op": "eq", "value": "BR"},
          {"field": "is_free", "op": "eq", "value": true}
        ]
      }
    ]
  },
  "fields": ["format", "country_code", "source_domain", "attrs.participation_requirements"],
  "bucket_limit": 10
}
```

Resposta parcial de estrutura (valores ilustrativos, não dados de staging):

```json
{
  "schema_version": "hackathon-facets-v1",
  "matched_total": 3,
  "counts_scope": "ALL_MATCHES_NOT_CURRENT_PAGE",
  "bucket_limit": 10,
  "facets": {
    "format": [{"value": "ONLINE", "count": 3}],
    "country_code": [{"value": null, "count": 2}, {"value": "BR", "count": 1}]
  }
}
```

## Campos e limites

Para evitar consultas custosas e injeção de SQL, o cliente só pode solicitar
**1 a 4 campos diferentes** e **1 a 15 buckets** por campo:

- Campos escalares: `format`, `status`, `event_type`, `country_code`,
  `edition_year`, `is_free`;
- Multivalorados: `track`, `technology`, `source_domain`;
- Atributos `attrs.*` apenas se constarem do catálogo curado,
  ativo e filtrável no banco, com tipo texto ou booleano.

Facetas numéricas arbitrárias não estão disponíveis nesta versão por
cardinalidade elevada. Para números, use `gte`, `between`, etc. no
construtor de filtros.

`value: null` representa **informação desconhecida ou ausente no canônico**,
não `false`, ausência real de prêmios ou restrição de participação.
Eventos com mais de uma fonte ou tecnologia podem aparecer em vários buckets;
somar contagens desses buckets superestima o total distinto.
`matched_total` é a contagem distinta correta.

O endpoint rejeita cursor (HTTP 422), campos repetidos, campos não autorizados
e expressões inválidas antes de abrir conexão. Os valores de filtros e caminhos
de atributos são enviados como parâmetros SQL; nomes de colunas e joins são
somente constantes em allowlists, nunca interpolação do usuário.

## Interface

A página `/advanced-search` mostra agora um painel de contagens reais e
permite escolher a dimensão a analisar, inclusive atributos texto/booleano
selecionados no banco. Mudar a dimensão recarrega apenas as contagens;
carregar mais resultados não altera a população agregada.

## Verificação

```bash
pytest -q apps/api/tests/test_search_facets.py

APP_ENV=test HIP_ALLOW_FACETS_DB_DRILL=1 \
DATABASE_URL="<disposable local PostgreSQL>" \
python -m scripts.ops.search_facets_integration_check
```

O teste PostgreSQL cria três hackathons sintéticos, quatro referências entre
duas fontes e um campo de participação selecionado com evidência. Verifica
filtros combinados, contagens exatas, desconhecidos, ausência de duplicação
por múltiplas referências e a diferença entre `matched_total` e o total
somado dos buckets multivalorados. Toda a transação é revertida.

## Limitações

As contagens refletem exclusivamente **os eventos presentes no banco no
instante da consulta**. Não são uma estimativa do número de hackathons
existentes na internet e não representam cobertura mundial. Os buckets
multivalorados e de valores desconhecidos precisam ser interpretados com
essa ressalva. O endpoint não remove as políticas de coleta por fonte e
não libera fontes atualmente pendentes de autorização.
