# Enriquecimento com evidências de participação e premiações — v1

A plataforma mantém o objetivo de agrupar edições únicas de hackathons globais,
enriquecendo-as sem preencher lacunas com suposições. Esta entrega faz uso das
observações e seleções canônicas já implementadas.

## Elegibilidade: o que é realmente conhecido

O atributo `participation_requirements` representa **texto literal publicado
pela fonte**, proveniente de `SourceEventRecord.eligibility_raw`. Pode vir de
requisitos presentes na API oficial Hack Club Events (`requirements`) ou
de `audience.name` / `audience.educationalRole` em JSON-LD de uma página
explicitamente autorizada.

- O texto é sanitizado superficialmente (`strip`) e limitado a 2.000 caracteres.
- A fonte, registro original e seletor de campo são preservados em
  `source_observations` e `canonical_field_selections`.
- A seleção atual preenche `eligibility_rules.eligibility_raw_text`, aparecendo
  no objeto `eligibility` da página pública.
- O filtro `attrs.participation_requirements` usa `contains`, `eq`,
  `is_known` e `is_unknown` com proveniência e consulta parametrizada.
- **NÃO se inferem** `student_required`, idade mínima, residência, países
  aceitos, equipes ou individualidade a partir de linguagem natural.

## Prêmios: evidência sem valores inventados

Algumas páginas de evento usam `award` ou `awards` no JSON-LD.
O parser aceita no máximo 10 labels expressos pelo editor da página, limita
cada nome a 240 caracteres e preserva o texto final em até 2.000 caracteres.

- Novo filtro `attrs.prize_details_text` e exposição em atributos públicos,
  incluindo fonte, data e confiança.
- Premiações **não** são convertidas em USD, somadas, nem introduzidas como
  valores verificados na tabela `prizes` ou `prize_pools`.
- A análise administrativa de completude reconhece um evento com uma
  seleção `prize_details_text` válida como tendo *algum detalhe de prêmio*.
  Isso não equivale a conhecer o prêmio monetário total.
- Links externos continuam sendo somente evidência e não autorização para
  fazer scraping em sites terceiros.

## Schema/API

A migração Alembic `0015_event_enrichment` apenas adiciona duas definições
curadas no catálogo `attribute_definitions`. Nenhuma nova fonte é autorizada.
Não são duplicadas as tabelas de fatos existentes.

Exemplo, após coleta autorizada e seleção canônica:

```json
{
  "expression": {
    "and": [
      {
        "field": "attrs.participation_requirements",
        "op": "contains",
        "value": "student"
      },
      {
        "field": "attrs.prize_details_text",
        "op": "is_known"
      }
    ]
  }
}
```

Condições com campo desconhecido permanecem desconhecidas; não significam
"evento não elegível" ou "evento sem prêmio".

## Testes e gates

- `packages/intelligence/tests/test_explicit_participation_evidence.py`:
  requisitos literais, tipos inválidos, limites e ausência de inferência.
- Testes do adapter JSON-LD verificam `award` e `audience` originais.
- A integração PostgreSQL de coleta controlada de duas fontes prova
  `source_event_refs → source_observations → canonical_field_selections →
  eligibility_rules → filtro attrs.*`, antes e depois de reprocessamento.
- `source_opportunities_integration_check.py` prova que um texto de prêmio
  selecionado reduz a lacuna de detalhes sem gerar valores monetários.
- CI realiza `upgrade head → downgrade base → upgrade head` incluindo
  a migração 0015.

## Limitações reais

A API Hack Club não foi tratada como fonte de prêmios que ela não publicou;
campos ausentes continuarão ausentes. Dados de premiação eventualmente
presentes em JSON-LD de outras plataformas só poderão ser coletados após
autorização por domínio ou contrato de integração. Estas melhorias são
estruturais e testadas com fixtures sintéticas; **nenhum crescimento real em
produção foi realizado ou medido neste PR**.
