# Revisão de lançamento baseada em evidências reais de staging (v1)

## Objetivo

Um CI verde atesta regressões e testes, mas **não** demonstra que a plataforma
possui hackathons atuais suficientes, campos decisórios úteis ou fontes
autorizadas coletadas com sucesso. A partir desta iteração, cada execução
manual **apply** em `staging-coverage` tenta produzir dois artefatos:

1. `hip-staging-coverage-receipt`: coleta observacional, antes/depois e
   métricas absolutas *agregadas* pós-campanha;
2. `hip-staging-launch-review`: avaliação de gates de lançamento, em modo
   **advisory**, independente do exit status da coleta.

Ambos são artefatos agregados, com retenção de 14 dias. Não incluem DSN,
credenciais, links individuais, documentos brutos ou `crawl_run_id`.

## Política versionada (fixa no código)

Ver `configs/coverage_release_policy_v1.json`. Trata-se do **mínimo para
solicitar revisão independente**, não de uma certificação de produção.

| Critério | Limiar mínimo |
| --- | --- |
| Perfil e fonte de evidência | `all`, as sete fontes; modo `apply` |
| Banco e permissões | Nome de staging, migrações atuais, sete fontes aprovadas |
| Execução | `MEASURED`, `SUCCEEDED`, evidência observacional confiável |
| Idade do snapshot | Até 72 horas, timezone explícito |
| Hackathons canônicos ativos | Pelo menos 250 |
| Hackathons com data futura | Pelo menos 20 |
| Hackathons efetivamente pesquisáveis | Pelo menos 200 |
| Com URL oficial conhecida | Pelo menos 80% do catálogo |
| Com datas de início e fim | Pelo menos 70% do catálogo |
| Com prazo de inscrição conhecido | Pelo menos 20% do catálogo |
| Backlog | Sem pendências/exaustão e sem fontes com situação não verificável |
| Consistência | Subcontagens nunca excedem o total canônico; ausências bloqueiam |

O limite de 250 reflete um ponto de referência histórico W2 documentado,
mas não é uma confirmação de contagem atual; os limites de qualidade são
**critérios operacionais iniciais**, sujeitos a revisão fundamentada em
dados de staging, e **não métricas observadas**.

A taxa de prazo conhecido representa somente `registration_close_at`
preenchido; não presume inscrição aberta nem elegibilidade. Dados ausentes
não se transformam em zero, `false` ou sucesso.

## Garantias do avaliador

`python -m scripts.ops.coverage_release_gate` não faz rede, não acessa
PostgreSQL, não executa scheduler ou deploy. Lê só o **recibo já
redigido** da campanha, de até 1 MiB, e usa a política revisada no Git.

Gera apenas:

- `NOT_READY`: qualquer requisito ausente ou abaixo do limiar;
- `UNVERIFIED`: recibo ausente, corrompido, grande demais ou inválido;
- `READY_FOR_INDEPENDENT_REVIEW`: todos os critérios cobertos por
  evidências adequadas e recentes.

**Nenhum desses resultados significa `PRODUCTION_READY`**. A revisão
humana ainda deve verificar permissões, direitos sobre dados, segurança,
monitoramento, acessibilidade, confiabilidade do produto e E2E com usuários.

`--advisory` mantém a execução de coleta independente de gates de revisão
que naturalmente não passam em campanhas iniciais. Sem `--advisory`, o
comando sai com código 1 quando não está pronto, permitindo uso como
bloqueio explícito em um processo de promoção separado.

Exemplo de execução offline:

```bash
python -m scripts.ops.coverage_release_gate \
  --receipt coverage-campaign-staging.json \
  --output launch-review.json
```

O teste sintético `test_coverage_release_gate.py` verifica que dry-runs,
fontes faltantes/bloqueadas, campo ausente, métricas contraditórias, datas
sem timezone, snapshots antigos, backlog não rastreado e perfil incompleto
impedem o resultado positivo.

## Próximo passo real

1. Provisionar e migrar PostgreSQL **staging dedicado**, com custos
   deliberadamente autorizados pelo operador (este PR não provisiona nada).
2. Criar `staging-coverage` protegido em GitHub → Settings → Environments.
3. Instalar `STAGING_DATABASE_URL` e `STAGING_DATABASE_NAME` **somente**
   nesse ambiente; revisar e aprovar cada fonte no DB.
4. Executar primeiro `plan`, depois `apply` com `dribdat` e limites
   mínimos. Esperar `NOT_READY` porque o portfólio é parcial — isso não
   invalida o valor da campanha de medição.
5. Ampliar para `all` depois de testar cada fonte, resolver backlog e
   revisar os números absolutos. Apenas então considerar revisão de
   lançamento independente e, separadamente, um plano de produção.

**Nenhuma campanha real nem revisão de lançamento foi declarada aprovada
nesta alteração.** A política é um gate de qualidade, não um mecanismo de
liberação de coleta de fontes sem autorização.
