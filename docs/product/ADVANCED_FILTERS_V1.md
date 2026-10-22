# Filtros compostos v1 — primeira entrega da busca universal

## Objetivo

Estender os filtros existentes sem substituí-los: permitir `AND`, `OR`, `NOT`
com condições em campos escalares, intervalos de datas/valores, presença de
informações e relacionamentos. Os campos suportados são registrados em código
e anunciados ao frontend; **isso não implementa ainda um esquema genérico de
atributos futuros**.

## API

- `GET /v1/hackathons/filter-fields` — catálogo tipado de campos e operadores.
- `POST /v1/hackathons/query` — busca com o mesmo `HackathonFilters` da
  API GET existente, acrescido de `expression` por meio de um modelo POST próprio.
- `/advanced-search` — construtor visual inicial que lê o catálogo da API, oferece\n  AND/OR e negação por condição e mostra os resultados com paginação.\n- `GET /v1/hackathons` — permanece compatível. O cursor da nova rota vincula
  também a expressão, impedindo reutilizá-lo com outra busca.

Exemplo, inscrição aberta, modalidade remota ou híbrida, brasileiros
e premiação de no mínimo USD 1.000 (quando informada):

```json
{
  "limit": 30,
  "sort": "deadline",
  "expression": {
    "and": [
      {"field": "status", "op": "eq", "value": "REGISTRATION_OPEN"},
      {"or": [
        {"field": "format", "op": "eq", "value": "ONLINE"},
        {"field": "format", "op": "eq", "value": "HYBRID"}
      ]},
      {"field": "eligible_country", "op": "eq", "value": "BR"},
      {"field": "prize_pool_usd", "op": "gte", "value": 1000}
    ]
  }
}
```

Os atributos relacionais `track`, `technology`, `organizer`, `sponsor`,
`source_domain` e `eligible_country` são avaliados com `EXISTS` contra as
tabelas canônicas. `organizer` inclui organizer/co-organizer/host; `sponsor`
inclui sponsor/prize partner/technology partner. País aceito também considera
`eligible_worldwide=true`. Campos textuais como país e modalidade usam os
valores codificados no banco.

### Valores ausentes e semântica de NOT

- `is_unknown`: SQL `IS NULL`, ou ausência de relacionamento conhecido.
- `is_known`: SQL `IS NOT NULL`, ou presença de relacionamento conhecido.
- `NOT` obedece à lógica SQL. Exemplo: `NOT(is_free=true)` **não**
  inclui `is_free=NULL`; procure desconhecidos explicitamente com `is_unknown`.
- Prêmios: `prize_pool_usd` usa o **maior fundo de prêmios registrado** em
  USD, não uma soma dos prêmios individuais nem uma conversão implícita.
- Datas exigem timestamps ISO 8601 com fuso/offset explícito; não presumimos UTC
  em prazos sem fuso.
- Participação elegível no Brasil não prova ausência de outras restrições
  de idade, categoria, emprego, graduação ou residência não estruturadas.

### Limites operacionais

- Profundidade máxima de árvore: 5.
- Até 32 nós por expressão.
- Até 12 filhos por grupo lógico.
- Até 20 valores por operador `in`.
- Texto de até 200 caracteres por termo.
- Operadores apenas por tipo de campo; sem nomes de colunas ou SQL do usuário.
- Valores sempre vinculados por parâmetros SQL; nenhum SQL livre.
- Limites e whitelists devem permanecer ativos também no frontend e na API.

## Testes

```bash
ruff check apps/api/src/hackathon_api apps/api/tests
ruff format --check apps/api/src/hackathon_api apps/api/tests
pytest -q apps/api/tests/test_advanced_filters.py apps/api/tests/test_query_builder.py
```

O CI deve validar o código integrado e o restante dos testes. A entrega
não ativa novas fontes nem altera o conteúdo do catálogo de eventos.

## Próxima etapa do plano global

1. Criar catálogo de atributos extras tipados no banco (com proveniência).
2. Projetar esses atributos no índice e registrar operadores dinamicamente.
3. Expor facetas, contagens e geração automática de controles de filtro.
4. Evoluir o construtor visual `/advanced-search` para grupos aninhados e\n   integração consistente com os filtros clássicos da página `/search`.
5. Expandir coletores autorizados, extração de detalhes e deduplicação.
6. Medir ganho em eventos únicos e completude com as ferramentas existentes.
