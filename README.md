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
  --rule tech-lead \
  --profile socratic \
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
| `--rule` | sim | regra (predefinida ou caminho), **sem** a extensão `.md`. Repetível. |
| `--profile` | não | perfil de personalidade (nome predefinido ou caminho `.yaml`). Se omitido, usa a personalidade padrão da rule |
| `--model` | sim | modelo no formato `provider/model` |
| `--fallback-model` | não | modelo alternativo usado **apenas na iteração atual** quando o principal retorna rate limit/quota (429). Sem fallback, o agente volta a dormir e reprocessa na próxima janela |
| `--repo` | sim | repositório `owner/repo` |
| `--project` | sim | número do Project V2 |
| `--workdir` | sim | diretório de trabalho (montado) |
| `--env` | não | `KEY=VALUE` exportada para o processo. Repetível (alternativa a `-e`) |
| `--interval` | não | segundos entre varreduras com a fila vazia (default `300`) |
| `--short-interval` | não | segundos entre tarefas após uma execução (default `30`) |
| `--rules-dir` | não | diretório das regras predefinidas (default `/app/rules`) |
| `--profiles-dir` | não | diretório dos perfis predefinidos (default `/app/profiles`) |

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
interactive: true       # opcional: age sobre novos comentários do usuário (não reprocessa o já respondido)
personality: >          # personalidade do agente (pré-definida pelo papel)
  Atencioso e detalhista...
---
# Tech Lead
... instruções ...
```

### Regras predefinidas (em `/app/rules/`)

| Regra | `statuses` | `role` | `unblock` |
|---|---|---|---|
| `product-owner` | `intent` | `product-owner` | — |
| `solutions-architect` | `feasibility` | `solutions-architect` | — |
| `systems-architect` | `design` | `systems-architect` | — |
| `framework-architect` | `plan` | `framework-architect` | — |
| `tech-lead` | `reviewing` | `tech-lead` | sim |
| `quality-assurance` | `qa-refinement`, `testing` | `qa` | — |
| `coder` | `coding` | `coder` | — |

Para usar uma regra customizada, monte o arquivo e passe o caminho (com ou sem `.md`):

```bash
docker run -d ... -v "$(pwd)/rules:/rules" ... \
  --rule meu-cargo
```

## Perfis de personalidade (em `/app/profiles/`)

Perfis são arquivos YAML que descrevem o comportamento/estilo da IA. Quando
`--profile` é informado, ele substitui a `personality` padrão da rule. Sem
`--profile`, o agente usa a `personality` definida no frontmatter de cada rule.

| Perfil | Ideia central |
|---|---|
| `socratic` | desconstruir certezas, causa raiz, imparcialidade (recomendado p/ Product Owner) |
| `nerd` | raciocínio profundo, listas, processo rigoroso |
| `professor` | mestre exigente que revisa e ensina |
| `firebrand` | energia, urgência e decisão por intuição |
| `gallant` | calor, cortesia e cuidado com as pessoas |
| `skeptic` | dúvida constante e investigação honesta |
| `goth` | mistério, charme sombrio e cultura geek |
| `pacifist` | harmonia, consenso e escuta ativa |
| `activist` | alegria contagiante e fervor por causas |

Estrutura universal de um perfil:

```yaml
profile_name: ""
descricao_estilo: ""

atributos_psicologicos:   # escalas 0 a 10
  entusiasmo: 0
  formalidade: 0
  confianca: 0
  metodismo: 0
  critica: 0
  empatia: 0
  jogo_de_cintura: 0

comunicacao:
  tom_de_voz: ""
  palavras_chave: []
  expressoes_proibidas: []
  uso_de_emojis: ""       # Nunca, Moderado, Frequente

bagagem_cultural:
  livros_inspiradores: []
  referencias_e_pessoas: []
  hobbies: []
  musicas_e_ritmos: []
  arquetipo: ""

diretrizes_de_comportamento: []
vieses_e_restricoes: []
```

Para usar um perfil customizado, monte o arquivo e passe o caminho:

```bash
docker run -d ... -v "$(pwd)/profiles:/profiles" ... \
  --profile /profiles/meu-perfil.yaml
```

## Menções entre agentes

Toda a comunicação entre agentes acontece nos **comentários da issue**. Para
acionar um agente, escreva `@NomeDoAgente` no comentário (o mesmo valor de
`--name`). O agente, ao varrer o quadro, detecta menções `@Nome` que ainda não
foram respondidas (não há comentário posterior assinado com `Nome: `) e responde.

Os agentes sempre assinam seus comentários com `Nome: ` no início.

## Evitando acionamentos repetidos (ralo de tokens)

Uma menção `@Nome` só é tratada como pendente se **não** houver um comentário
posterior assinado com `Nome: `. Ou seja: assim que o agente responde, a menção
fica "resolvida" e não volta a acionar o agente nas varreduras seguintes — sem
precisar editar/desmarcar o comentário.

Para papéis que **ficam no mesmo status** durante uma conversa (ex.: o
Product Owner em `intent`, conversando com o cliente antes de criar a RFC), use
`interactive: true` no frontmatter da rule. Nesse modo o agente só processa um
item quando há atividade nova do usuário (item sem comentários ou último
comentário não assinado por ele), evitando reprocessar a mesma issue a cada
ciclo.

## Quadro esperado

O Project V2 deve ter estes campos:

- `Status` (single-select): `intent → feasibility → design → plan → qa-refinement → coding → reviewing → testing → done` (fluxo linear; itens **não voltam** — correção vira nova issue).
- `Blocked` (single-select, opção `blocked`): flag booleana independente do status.
- `BlockedBy` (texto): quando bloqueado, indica qual tarefa/issue está bloqueando (ex.: `RFC-2`). Usado pelo Tech Lead para decidir o desbloqueio.
- `Role` (single-select): `product-owner | solutions-architect | systems-architect | framework-architect | tech-lead | qa | coder`.
- `Worker` (**texto**): nome do agente em execução. Deve ser texto (não single-select) para que o nome seja livre — cada pessoa escolhe o `--name` ao subir o agente.

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
python3 src/cli.py --name Davi --rule tech-lead \
  --model deepseek/deepseek-v4-pro --fallback-model deepseek/deepseek-v4-flash \
  --repo OWNER/REPO --project 1 --workdir .
```
