# Ampliação global das fontes de hackathons — 22/10/2026

## Objetivo

Cobrir progressivamente os hackathons públicos e verificáveis em múltiplas fontes,
preservando fatos, links originais e conflitos, sem alegar cobertura de 100% da web.
**Uma fonte cadastrada NÃO significa que está autorizada, sendo coletada ou produzindo eventos.**

O pipeline existente já possui normalização, reconciliação, controles operacionais e
proveniência por campo. Este incremento adiciona os cinco sites que faltavam na
solicitação, os links específicos dos seis sites já registrados, descoberta limitada
de páginas internas de eventos em HTML e uma fila persistente para esse modo.

## Fontes da solicitação

| Site | Chave de catálogo | Situação após este PR |
| --- | --- | --- |
| HackerEarth | `hackerearth` | Link solicitado incluído; revisão necessária |
| HackathonRadar | `hackathonradar` | Nova fonte; revisão de termos e uso de dados necessária |
| Devpost | `devpost` | Link incluído; **RESTRICTED**, sem coleta automática |
| TAIKAI | `taikai` | Filtro/listagem BR incluído; revisão necessária |
| Devfolio | `devfolio` | Filtro de inscrições abertas incluído; revisão necessária |
| WeMakeDevs | `wemakedevs` | Nova listagem oficial; revisão necessária |
| MLH | `mlh` | Link de temporada 2026 incluído e host explícito `www.mlh.com`; revisão necessária |
| DoraHacks | `dorahacks` | Filtro de eventos em andamento incluído; revisão necessária |
| HackaTrack | `hackatrack` | Nova fonte; revisão necessária |
| Hackathons Near Me | `hackathonsnear` | Nova fonte; acesso pode estar limitado |
| lablab.ai | `lablab_ai` | Nova listagem de AI hackathons; revisão necessária |

O Devpost explicitamente restringe scraping/crawling automatizado em seus termos.
Usar API ou acordo formal só depois de autorização. Não desabilitar os checks
do catálogo/PostgreSQL para contornar esse problema.

## O que a nova descoberta HTML faz

Somente para fontes que um operador tenha aprovado com `permission_status=ALLOWED`
e `detail_extraction_enabled=true`:

1. Abre uma URL-semente respeitando políticas/robots/limites de tamanho e rede.
2. Analisa até 1.500 âncoras por página, enfileirando até 100 links de detalhes
   ou links de paginação visivelmente identificados.
3. Só aceita URLs HTTP(S) do host configurado ou seus `allowed_hosts`.
   URLs externas de inscrição, patrocínio e organizadores não viram autorização.
4. Usa `source_url_backlog` no PostgreSQL para retomar a coleta HTML em execuções
   seguintes, mesmo com `max_pages` baixo. Paginação HTML tem máximo de 50 saltos.
5. Analisa `schema.org/Event` JSON-LD nas páginas acessadas, sem inventar
   campos ausentes; o processamento canônico existente resolve o restante.

**Limitação importante:** SPA/JS client-side, paginação por APIs privadas,
links sem estrutura reconhecível e dados que aparecem só em modal ou login
ainda exigem conectores específicos e revisão de acesso. O extrator JSON-LD
também não captura todos os campos de todos os formatos HTML. Não prometa
cobertura integral sem medir eventos e completude por campo.

## Fontes adicionais (sem alterar o catálogo automaticamente)

Pesquisas, feeds, diretórios, organizadores, universidades e parceiros
podem produzir URLs candidatas. Depois de obter URLs legitimamente por um
provedor autorizado, grave uma por linha em `candidates.txt` e execute:

```bash
python scripts/ops/propose_hackathon_sources.py --input candidates.txt --output proposals.json
python scripts/source_coverage_report.py --format markdown
pytest -q packages/source_sdk/tests/test_global_source_registry.py \
  packages/source_sdk/tests/test_html_listing_discovery.py \
  packages/source_sdk/tests/test_source_proposals.py
```

O comando de propostas **não faz rede, grava banco nem modifica o catálogo**.
Agrupa por domínio, rejeita URLs inseguras e entrega fontes com
`permission_status=REVIEW_REQUIRED` e coleta desabilitada.
A revisão humana deve verificar relevância, domínio oficial, termos de uso,
robots, método disponível (API/feed/sitemap/HTML), direitos de redistribuição,
rate limits, dados pessoais e mapeamento de campos. Só então incorporar ao
catálogo e configurar o banco por fonte; nenhuma permissão é promovida via seeds.

## Meta de completude dos eventos

Persistir observações verificadas para identidade/edição, datas e fuso,
status e prazo de inscrição, URL de inscrição, regras de elegibilidade,
tipo e localização do evento, faixa etária, tamanho da equipe, custo,
premiação e moeda, tracks/temas, tecnologias, organizadores, patrocinadores,
jurados/mentores, cronograma, idiomas, links oficiais, últimas atualizações
e proveniência. **Dado ausente = desconhecido**, não `false` ou `0`.

Priorizar por etapas:

- **Etapa 1** — Revisar os 11 sites, validar páginas e negociar APIs/licenças.
- **Etapa 2** — Adapters próprios para paginação/JSON/HTML e testes com fixtures,
  começando pelas fontes com permissão e maior yield canônico.
- **Etapa 3** — Busca autorizada em múltiplas línguas para descoberta recorrente
  de novos sites, com fila de revisão e observabilidade.
- **Etapa 4** — Indicadores por fonte: sucesso do crawl, eventos candidatos,
  `CREATED/LINKED/UPDATED/REJECTED`, deduplicados por edição,
  cobertura de cada campo, data da última verificação e taxa de falsos positivos.

### Critérios de aceitação ainda pendentes

- Cada fonte passa revisão jurídica/operacional **independente**.
- Coletores testados individualmente, com dados reais consentidos/permitidos.
- Integração PostgreSQL e CI passam com a migration existente `0011`
  (fila `source_url_backlog`) e migrações posteriores.
- O painel comprova eventos reais distintos + completude, sem misturar
  contadores de URL, payload e hackathon único.
- Nenhum job automático ou permissão de produção é ativado por este PR.
