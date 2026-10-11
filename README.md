# agent-watcher

Autonomous agent that operates a **GitHub Kanban board (Project V2)** inside a
Docker container, using [opencode](https://opencode.ai) as the AI runtime.

Runs on any machine with Docker — without depending on the client's environment
for `opencode`, `gh` or Python. You just mount the project directory and pass
credentials via environment variables.

## How it works

Each container is **one agent** (one role). It:

1. Scans the board looking for issues in the status(es) of its role (no `Worker` and no `Blocked`).
2. Checks pending mentions (`@Name` in the comments) and replies.
3. Claims the issue (`Worker` = agent name).
4. Invokes opencode with the role's rules + the issue.
5. opencode itself does the work (code, tests, PR, review…) and updates the board.
6. Releases the `Worker` and repeats.

## Usage

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

### Parameters

| Flag | Req. | Description |
|---|---|---|
| `--name` | yes | agent identity (signs comments and the `Worker` field) |
| `--rule` | yes | rule (predefined or path), **without** the `.md` extension. Repeatable. |
| `--profile` | no | personality profile (predefined name or `.yaml` path). If omitted, uses the rule's default personality |
| `--model` | yes | model in `provider/model` format |
| `--fallback-model` | no | fallback model used **only in the current iteration** when the primary returns rate limit/quota (429). Without a fallback, the agent goes back to sleep and reprocesses on the next window |
| `--repo` | yes | `owner/repo` repository |
| `--project` | yes | Project V2 number |
| `--workdir` | yes | working directory (mounted) |
| `--env` | no | `KEY=VALUE` exported to the process. Repeatable (alternative to `-e`) |
| `--interval` | no | seconds between scans when the queue is empty (default `300`) |
| `--short-interval` | no | seconds between tasks after a run (default `30`) |
| `--rules-dir` | no | predefined rules directory (default `/app/rules`) |
| `--profiles-dir` | no | predefined profiles directory (default `/app/profiles`) |

> `GH_TOKEN` is required (`gh` reads it from `GH_TOKEN`/`GITHUB_TOKEN`). Provider
> keys (e.g. `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`) are read by opencode from
> the environment.

## Rate limit and cache

The loop handles GitHub errors without stopping the agent: on rate limit
(`429`/`secondary rate limit`) it does exponential *backoff* (up to 3600s) and
tries again; other errors only log and the agent goes back to sleep. Items left
with the agent's own `Worker` after an interruption are recovered at the start
of each cycle.

To reduce API calls, the **whole** board (items + fields + comments) is fetched
in a single GraphQL query and cached in `.agent-watcher-cache-<project>.json` in
the workdir (shared between agents). A *lock* ensures that, when the TTL
expires, **only one** agent re-runs the fetch — the others read the cache (or the
stale one). The TTL is configurable via `AW_CACHE_TTL` (default **60s**) and the
cache is invalidated on every mutation (`set`/`clear`/`create`).

On GitHub rate limit/failure, the agent falls back to the **stale cache** and
keeps operating without new requests. **Add the file to the `.gitignore` of the
mounted repository** so the reviewer agent doesn't accidentally commit it.

## Rules

Rules define **what the agent does** and are composable. Each `--rule` is a
markdown with frontmatter:

```markdown
---
statuses: [reviewing]   # board columns this rule consumes (order = priority)
role: tech-lead         # value of the Role field
unblock: true           # optional: this agent also handles blocked items
interactive: true       # optional: acts on new user comments instead of reprocessing
personality: >          # agent personality (predefined by the role)
  Attentive and detail-oriented...
---
# Tech Lead
... instructions ...
```

### Predefined rules (in `/app/rules/`)

| Rule | `statuses` | `role` | `unblock` |
|---|---|---|---|
| `product-owner` | `intent` | `product-owner` | — |
| `solutions-architect` | `feasibility` | `solutions-architect` | — |
| `systems-architect` | `design` | `systems-architect` | — |
| `framework-architect` | `plan` | `framework-architect` | — |
| `tech-lead` | `reviewing` | `tech-lead` | yes |
| `quality-assurance` | `qa-refinement`, `testing` | `qa` | — |
| `coder` | `coding` | `coder` | — |

To use a custom rule, mount the file and pass the path (with or without `.md`):

```bash
docker run -d ... -v "$(pwd)/rules:/rules" ... \
  --rule my-role
```

## Personality profiles (in `/app/profiles/`)

Profiles are YAML files describing the AI's behavior/style. When `--profile` is
provided, it replaces the rule's default `personality`. Without `--profile`, the
agent uses the `personality` defined in each rule's frontmatter.

| Profile | Core idea |
|---|---|
| `socratic` | deconstruct certainties, root cause, impartiality (recommended for Product Owner) |
| `nerd` | deep reasoning, lists, rigorous process |
| `professor` | demanding master who reviews and teaches |
| `firebrand` | energy, urgency and intuition-driven decisions |
| `gallant` | warmth, courtesy and care for people |
| `skeptic` | constant doubt and honest investigation |
| `goth` | mystery, dark charm and geek culture |
| `pacifist` | harmony, consensus and active listening |
| `activist` | contagious joy and fervor for causes |

Universal profile structure:

```yaml
profile_name: ""
style_description: ""

psychological_attributes:   # 0 to 10 scales
  enthusiasm: 0
  formality: 0
  confidence: 0
  methodicalness: 0
  criticality: 0
  empathy: 0
  adaptability: 0

communication:
  tone_of_voice: ""
  keywords: []
  forbidden_expressions: []
  emoji_usage: ""            # Never, Moderate, Frequent

cultural_background:
  inspiring_books: []
  references_and_people: []
  hobbies: []
  music_and_rhythms: []
  archetype: ""

behavior_guidelines: []
biases_and_restrictions: []
```

To use a custom profile, mount the file and pass the path:

```bash
docker run -d ... -v "$(pwd)/profiles:/profiles" ... \
  --profile /profiles/my-profile.yaml
```

## Mentions between agents

All communication between agents happens in the **issue comments**. To trigger an
agent, write `@AgentName` in a comment (the same value as `--name`). The agent,
when scanning the board, detects `@Name` mentions that have not been answered yet
(no later comment signed with `Name: `) and replies.

Agents always sign their comments with `Name: ` at the start.

## Avoiding repeated triggers (token drain)

A `@Name` mention is only treated as pending if there is **no** later comment
signed with `Name: `. In other words: as soon as the agent replies, the mention
is "resolved" and no longer triggers the agent on subsequent scans — without
editing/unmarking the comment.

For roles that **stay in the same status** during a conversation (e.g. the
Product Owner in `intent`, talking to the client before creating the RFC), use
`interactive: true` in the rule's frontmatter. In this mode the agent only
processes an item when there is new user activity (an item with no comments, or
whose last comment is not signed by it), avoiding reprocessing the same issue
every cycle.

## Language

Agents always reply **in the language the issue was opened in** (title and body):
if the issue is in Portuguese, the replies are in Portuguese; if in Korean, in
Korean; and so on.

## Expected board

The Project V2 must have these fields:

- `Status` (single-select): `intent → feasibility → design → plan → qa-refinement → coding → reviewing → testing → done` (linear flow; items **do not go back** — a fix creates a new issue).
- `Blocked` (single-select, option `blocked`): boolean flag independent of status.
- `BlockedBy` (text): when blocked, indicates which task/issue is blocking (e.g. `RFC-2`). Used by the Tech Lead to decide on unblocking.
- `Role` (single-select): `product-owner | solutions-architect | systems-architect | framework-architect | tech-lead | qa | coder`.
- `Worker` (**text**): name of the agent currently running. Must be text (not single-select) so the name is free — each person chooses the `--name` when starting the agent.

## Building the image

```bash
docker build -t agent-watcher .
```

Publish (Docker Hub):

```bash
docker tag agent-watcher esdrasschonevald/agent-watcher:latest
docker push esdrasschonevald/agent-watcher:latest
```

## Local development (without Docker)

```bash
pip install pyyaml        # (or apk add py3-yaml)
export GH_TOKEN=... AW_OWNER=... AW_REPO=... AW_PROJECT=...
python3 src/cli.py --name Davi --rule tech-lead \
  --model deepseek/deepseek-v4-pro --fallback-model deepseek/deepseek-v4-flash \
  --repo OWNER/REPO --project 1 --workdir .
```
