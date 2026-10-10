# agent-watcher

Agente autônomo que opera um quadro **Kanban do GitHub (Project V2)** dentro de um
container Docker, usando [opencode](https://opencode.ai) como runtime de IA.

Roda em qualquer máquina com Docker — sem depender do ambiente do cliente para
`opencode`, `gh` ou Python. Você só monta o diretório do projeto e passa as
credenciais via variáveis de ambiente.

## Como funciona

Cada container é **um agente** (um papel). Ele:

1. Varre o quadro procurando issues no(s) status(s) do seu papel (sem `Worker` e sem `Blocked`).
2. Verifica menções pendentes (`@Nome` nos comentários) e responde.
3. Reclama a issue (`Worker` = nome do agente).
4. Invoca o opencode com as regras do papel + a issue.
5. O próprio opencode faz o trabalho (código, testes, PR, revisão…) e atualiza o quadro.
6. Libera o `Worker` e repete.

## Uso

```bash
docker run -d \
  --name davi \
  -v "$(pwd):/workspace" \
  -e GH_TOKEN="$GH_TOKEN" \
  -e DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" \
  esdrasschonevald/agent-watcher \
  --name Davi \
  --rule tech-lead.md \
  --model deepseek/deepseek-v4-pro \
  --fallback-model deepseek/deepseek-v4-flash \
  --repo phprise-foundation/koenma-id \
  --project 1 \
  --workdir /workspace
```

### Parâmetros

| Flag | Obrig. | Descrição |
|---|---|---|
| `--name` | sim | identidade do agente (assina comentários e o campo `Worker`) |
| `--rule` | sim | regra (predefinida ou caminho). Repetível. |
| `--model` | sim | modelo no formato `provider/model` |
| `--fallback-model` | não | modelo alternativo usado **apenas na iteração atual** quando o principal retorna rate limit/quota (429). Sem fallback, o agente volta a dormir e reprocessa na próxima janela |
| `--repo` | sim | repositório `owner/repo` |
| `--project` | sim | número do Project V2 |
| `--workdir` | sim | diretório de trabalho (montado) |
| `--env` | não | `KEY=VALUE` exportada para o processo. Repetível (alternativa a `-e`) |
| `--interval` | não | segundos entre varreduras com a fila vazia (default `300`) |
| `--short-interval` | não | segundos entre tarefas após uma execução (default `30`) |
| `--rules-dir` | não | diretório das regras predefinidas (default `/app/rules`) |

> `GH_TOKEN` é obrigatório (o `gh` lê de `GH_TOKEN`/`GITHUB_TOKEN`). As chaves de
> provider (ex.: `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`) são lidas pelo opencode a
> partir do ambiente.

## Rate limit e cache

O loop trata erros do GitHub sem encerrar o agente: em caso de rate limit
(`429`/`secondary rate limit`), ele faz *backoff* exponencial (até 3600s) e tenta
de novo; outros erros apenas logam e o agente volta a dormir. Itens que ficaram
com o `Worker` do próprio agente após uma interrupção são recuperados no início
de cada ciclo.

Para reduzir chamadas ao GraphQL, o resultado de `kanban list` é cacheado por
30s em `.agent-watcher-cache-<project>.json` no workdir (compartilhado entre os
agentes), e invalidado a cada mutação (`set`/`clear`/`create`). **Adicione esse
arquivo ao `.gitignore` do repositório montado** para que o agente revisor não o
commite acidentalmente.

## Regras

Regras definem **o que o agente faz** e são composáveis. Cada `--rule` é um
markdown com frontmatter:

```markdown
---
statuses: [reviewing]   # colunas que esta regra consome (ordem = prioridade)
role: tech-lead         # valor do campo Role
unblock: true           # opcional: este agente também cuida de itens bloqueados
personality: >          # personalidade do agente (pré-definida pelo papel)
  Atencioso e detalhista...
---
# Tech Lead
... instruções ...
```

### Regras predefinidas (em `/app/rules/`)

| Regra | `statuses` | `role` | `unblock` |
|---|---|---|---|
| `solutions-architect.md` | `feasibility` | `solutions-architect` | — |
| `systems-architect.md` | `design` | `systems-architect` | — |
| `framework-architect.md` | `plan` | `framework-architect` | — |
| `tech-lead.md` | `reviewing` | `tech-lead` | sim |
| `quality-assurance.md` | `qa-refinement`, `testing` | `qa` | — |
| `coder.md` | `coding` | `coder` | — |

> O `product-owner` (status `intent`) fica para uma fase futura.

Para usar uma regra customizada, monte o arquivo e passe o caminho:

```bash
docker run -d ... -v "$(pwd)/rules:/rules" ... \
  --rule /rules/meu-cargo.md
```

## Menções entre agentes

Toda a comunicação entre agentes acontece nos **comentários da issue**. Para
acionar um agente, escreva `@NomeDoAgente` no comentário (o mesmo valor de
`--name`). O agente, ao varrer o quadro, detecta menções `@Nome` que ainda não
foram respondidas (não há comentário posterior assinado com `Nome: `) e responde.

Os agentes sempre assinam seus comentários com `Nome: ` no início.

## Quadro esperado

O Project V2 deve ter estes campos:

- `Status` (single-select): `intent → feasibility → design → plan → qa-refinement → coding → reviewing → testing → done` (fluxo linear; itens **não voltam** — correção vira nova issue).
- `Blocked` (single-select, opção `blocked`): flag booleana independente do status.
- `BlockedBy` (texto): quando bloqueado, indica qual tarefa/issue está bloqueando (ex.: `RFC-2`). Usado pelo Tech Lead para decidir o desbloqueio.
- `Role` (single-select): `product-owner | solutions-architect | systems-architect | framework-architect | tech-lead | qa | coder`.
- `Worker` (single-select): nome do agente em execução.

## Build da imagem

```bash
docker build -t agent-watcher .
```

Publicar (Docker Hub):

```bash
docker tag agent-watcher esdrasschonevald/agent-watcher:latest
docker push esdrasschonevald/agent-watcher:latest
```

## Desenvolvimento local (sem Docker)

```bash
pip install pyyaml        # (ou apk add py3-yaml)
export GH_TOKEN=... AW_OWNER=... AW_REPO=... AW_PROJECT=...
python3 src/cli.py --name Davi --rule tech-lead.md \
  --model deepseek/deepseek-v4-pro --fallback-model deepseek/deepseek-v4-flash \
  --repo OWNER/REPO --project 1 --workdir .
```
