# Hack Club YSWS — integração oficial de programas (filtro estrito de hackathons)

## Origem e autorização técnica

Documentação pública do próprio Hack Club:

- [Repositório oficial `hackclub/events-api`](https://github.com/hackclub/events-api)
- `GET https://events-api.hackclub.dev/api/programs`: API REST pública,
  somente leitura, sem autenticação, para listar todos os **programas públicos
  listados**. Não é um diretório exclusivo de hackathons.
- O README documenta limite de **100 requisições a cada 15 minutos**, cache
  de cinco minutos e endpoints opcionais por status. A plataforma limita a
  fonte a **4 requisições/minuto**, concorrência 1, um seed, atualizações
  a cada seis horas. Não utiliza `include_unlisted` ou chave privada.

A fonte `hackclub_ysws_programs` foi cadastrada como `ALLOWED` para o
endpoint explicitamente público, porém **a execução ainda exige aprovação
independente no PostgreSQL**. O registry nunca remove pausa, sobrescreve
aprovação manual ou autoriza novas URLs por existir uma URL de organizador
na resposta. Políticas e eventuais alterações de termos devem ser revisadas
antes de utilizar em staging/produção.

## Campos e proveniência

O adaptador `HackClubYswsProgramsAdapter` só aceita
`success=true` com `data` em forma de lista. Campos reconhecidos
conforme o README:

| Campo da API | Campo da plataforma | Interpretação |
| --- | --- | --- |
| `id` | `source_event_id` | Identidade estável da publicação |
| `Name` | `name` | Nome publicado, NÃO prova tipo hackathon |
| `Start Date` / `End Date` | Datas originais | Somente datas explícitas |
| `Website URL` | `official_url` | Evidência, nunca autorização de fetch |
| `Icon – CDN Link` | `metadata.icon_url` | URL da mídia original |
| `HCB` | `metadata.hcb_url` | Conta/organização, NÃO premiação ou taxa |
| `createdTime` | `metadata.source_created_at` | Publicação original, não data de abertura |

Apenas URLs HTTP(S) sintaticamente válidas e sem credenciais são preservadas.
Linhas sem `id` ou `Name` são ignoradas. Respostas com mais de 5000
entradas falham explicitamente — o sistema **não marca uma coleta truncada
como completa**. A API oficial não documenta paginação desse endpoint, por
isso `durable_api_pages=false`: a lista recebida não comprova cobertura
completa de todos os programas passados ou não listados.

O `prepare_event` existente classifica somente nomes/tags com sinais
positivos como hackathon, datathon, buildathon etc. Workshop, aula, AMA e
YSWS sem classificação clara são **rejeitados**, preservando a evidência
bruta de sua publicação para auditoria.

## Uso

Simulação sem banco nem rede:

```bash
python -m scripts.ops.controlled_catalog_ingestion \
  --source hackclub_ysws_programs
```

Amostra controlada em staging, após revisão do domínio e aprovação no DB:

```bash
APP_ENV=staging HIP_ALLOW_LIVE_INGESTION=1 \
DATABASE_URL="<staging PostgreSQL>" \
python -m scripts.ops.controlled_catalog_ingestion \
  --source hackclub_ysws_programs --max-pages 1 \
  --max-versions 100 --apply
```

Ou combine com outras APIs oficiais pelo comando
`python -m scripts.ops.coverage_campaign`, preservando a medição de
variação líquida global distinta e fontes sobrepostas.

## Verificações

- Testes offline `test_hackclub_ysws_public_api.py`: validade do
  registry, campos Airtable reais, classificação conservadora,
  valores opcionais inválidos e limite de 5000 registros.
- Teste PostgreSQL com HTTP sintético
  `scripts.ops.ysws_public_api_integration_check`: dois hackathons
  elegíveis, dois programas não pertinentes, seleções canônicas
  com proveniência e reexecução sem novos canônicos.
- Migrações, worker Redis/Celery, backup/restore, web, segurança e
  tipagem continuam obrigatórios.

**Nenhuma quantidade real de hackathons do endpoint foi medida neste PR.**
Os registros de teste são sintéticos. A integração amplia a capacidade de
coleta; os números de aumento real só existirão após execução autorizada
em staging.
