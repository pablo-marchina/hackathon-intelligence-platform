# OpenData.ch Hackdays — origem pública e coberturas (v1)

## Nova fonte

- **Instância:** [Opendata.ch / Hackdays](https://new-hack.opendata.ch/about/)
- **Endpoint autodeclarado:** `GET https://new-hack.opendata.ch/api/events.json`
- **Source key:** `dribdat_opendata_ch`
- **Formato:** Dribdat JSON `{"events":[...]}`
- **Revisado:** 28 de outubro de 2026, endpoint e feed públicos verificados.
- **Permissão do cadastro:** `ALLOWED` apenas para a API expressamente pública; o
  operador precisa aprovar a fonte separadamente no banco PostgreSQL.

A API do organizador publica um inventário multi-edição de iniciativas
suíças como Energy Data Hackdays, Open Farming Hackdays, GovTech Hackathon,
Data Hackdays Uri, Smart Regio Lab, GLAMhack e eventos gerais sem natureza de
hackathon. **Não presumir que todos os itens são hackathons.**

A fonte é independente de `dribdat_glamhack`; pode conter **edições
duplicadas** entre os feeds. As contagens por origem não são aditivas.
O vínculo canônico só é realizado com evidências suficientemente fortes
(identidade + URL oficial + nome + data conforme política existente).

## Coleta e controles

O adaptador `DribdatOpenEventsAdapter` foi reutilizado sem criar um
crawler específico. Apenas o host `new-hack.opendata.ch` é autorizado;
URLs `webpage_url` dos organizadores são salvas como evidência e **não
são visitadas automaticamente**. Máximo de 2 requests/minuto e 1
requisição simultânea; refresh mínimo 12 horas, sem paginação inventada.

O normalizador considera apenas sinal positivo no nome (ex. Hackdays,
Hackathon, GLAMhack) ou **hashtag exata declarada pela própria fonte**
(`#hackathon`, `#hackdays`, `#datathon`, `#buildathon` e `#glamhack`).
Termos parcialmente coincidentes como `#notahackathon` e
`#hackathon2026` não são classificados por substring. Campos ausentes
não são inferidos: gratuidade, inscrição aberta, critérios de idade,
países elegíveis e premiação permanecem desconhecidos.

As datas históricas são mantidas como datas originais RFC 2822 com
timezone explícito; não são eventos futuros. Os resumos publicados são
armazenados com a proveniência de campo da API, sem acessar links externos.

## Testes

- Teste offline de nomes, tags exatas, datas, resumos e exclusão de
  eventos gerais no `test_dribdat_public_api.py`.
- E2E PostgreSQL em `scripts.ops.opendata_hackdays_integration_check`
  com HTTP completamente sintético, verificando duas edições aceitas,
  um festival rejeitado, campo selecionado com origem e repetição sem
  novos canônicos.
- O CI padrão continua executando migrations, Celery/Redis,
  frontend, segurança e recuperação.

```bash
pytest -q packages/source_sdk/tests/test_dribdat_public_api.py

APP_ENV=test HIP_ALLOW_OPENDATA_DB_DRILL=1 \
DATABASE_URL="<PostgreSQL local descartável migrado>" \
python -m scripts.ops.opendata_hackdays_integration_check
```

## Staging e lançamento

O workflow manual `staging-coverage` agora tem **três fontes no perfil
`dribdat` e oito no perfil `all`**. O gate versionado de revisão de
lançamento exige as oito fontes, para impedir um resultado
`READY_FOR_INDEPENDENT_REVIEW` com um portfólio parcial. Isso não altera
os limiares absolutos de qualidade nem autoriza produção.

Primeiro execute `plan`, depois apenas com um banco de staging
provisionado, migrado e explicitamente autorizado faça `apply` e
analise o recibo de crescimento líquido. **Nenhum hackathon real foi
importado nesta alteração.** O aumento de cobertura será medido no
primeiro recibo real de staging.
