"""Loop do agent-watcher: varre o quadro, reclama tarefas e invoca o opencode."""
import datetime
import subprocess
import sys
import tempfile
import time

import kanban
import rules

STATUSES_HELP = ("intent, feasibility, design, plan, qa-refinement, "
                 "coding, reviewing, testing, done")

LLM_RATE_LIMIT_MARKERS = (
    "rate limit", "rate_limit", "429", "insufficient_quota", "insufficient quota",
    "quota", "billing", "too many requests", "overloaded",
)


def _llm_rate_limited(text):
    low = (text or "").lower()
    return any(m in low for m in LLM_RATE_LIMIT_MARKERS)


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def build_prompt(config, number=None, title=None, mention=None):
    name = config["name"]
    rule_bodies = "\n\n".join(r.body for r in config["rules"])
    profile = config.get("profile_personality")
    if profile:
        personalities = profile
    else:
        personalities = "\n\n".join(r.personality for r in config["rules"] if r.personality)

    if mention:
        task_section = (
            f"Você foi mencionado na issue #{mention['number']} ({mention['title']}).\n"
            f"Trecho da menção: {mention['mention']}\n"
            f"- Leia a issue e os comentários: gh issue view {mention['number']} --repo {config['repo']} --comments\n"
            f"- Responda a cada menção a @{name} que ainda não tenha resposta, assinando com '{name}: '.\n"
            f"- Ao terminar, limpe o Worker: kanban clear {mention['number']} --field Worker\n"
        )
    else:
        task_section = (
            f"Trabalhe na issue #{number} do repositório {config['repo']}.\n"
            f"- Leia a issue e os comentários: gh issue view {number} --repo {config['repo']} --comments\n"
            f"- Comunique-se SEMPRE via comentário, assinando com '{name}: '.\n"
            f"- Ao finalizar, limpe o Worker: kanban clear {number} --field Worker\n"
        )

    return f"""Você é "{name}", um agente autônomo de desenvolvimento que atua em um quadro Kanban do GitHub (Project V2). Não há supervisor em tempo real: sua comunicação e o estado do trabalho vivem nas issues do GitHub.

# Personalidade

{personalities or "(não definida)"}

# Regras do seu papel

{rule_bodies}

# Comunicação (via comentários na issue)

- Toda comunicação acontece nos comentários da issue.
- Para marcar/acionar um agente, escreva `@NomeDoAgente` no comentário.
- Sempre assine seus comentários com `{name}: `.
- Ao trabalhar numa issue, leia os comentários e responda a toda menção `@{name}` que ainda não tenha resposta sua.

# Interação com o quadro (helper `kanban`)

- Consultar: kanban list [--status <x>] [--blocked] [--json]
- Menções pendentes: kanban mentions <nome>
- Mudar estado: kanban set <n> --status <x> [--role <r>] [--worker <w>] [--blocked|--unblocked] [--blocked-by <tarefa>|--clear-blocked-by]
- Limpar campo: kanban clear <n> --field Worker
- Comentar: kanban comment <n> --text "..."
- Criar issue: kanban create --title "..." --body-file <arquivo.md> [--status <x>] [--role <r>] [--blocked-by <tarefa>]

Statuses válidos (fluxo linear, os itens NÃO voltam; correção vira nova issue):
{STATUSES_HELP}

# Tarefa atual

{task_section}
"""


def run_agent(config, model, prompt, number=None):
    tag = f" para #{number}" if number is not None else ""
    log(f"invocando opencode{tag} ({model})")
    with tempfile.TemporaryFile() as errf:
        r = subprocess.run(
            ["opencode", "run", "--auto", "--print-logs", "-m", model,
             "--dir", config["workdir"], prompt],
            stdout=None, stderr=errf,
        )
        errf.seek(0)
        err_text = errf.read().decode("utf-8", "replace")
    if err_text:
        sys.stderr.write(err_text)
    if r.returncode == 0:
        return "ok"
    log(f"opencode terminou com código {r.returncode}")
    if _llm_rate_limited(err_text):
        return "rate_limited"
    return "error"


def process(config, name, number, title=None, mention=None):
    """Reclama, roda o opencode (com fallback) e libera o Worker."""
    kanban.set_field(number, "Worker", name)
    prompt = build_prompt(config, number=number, title=title, mention=mention)
    result = run_agent(config, config["model"], prompt, number)
    if result == "rate_limited":
        fallback = config.get("fallback_model")
        if fallback:
            log(f"rate limit no modelo principal; tentando fallback {fallback} em #{number}")
            result = run_agent(config, fallback, prompt, number)
    kanban.clear_field(number, "Worker")
    return result


def next_task(rows, statuses):
    out = [i for i in rows
           if i["status"] in statuses and not i["worker"] and not i["blocked"]]
    out.sort(key=lambda i: (statuses.index(i["status"]) if i["status"] in statuses else 99,
                            kanban.sort_key(i)))
    return out[0] if out else None


def next_blocked(rows):
    out = [i for i in rows if i["blocked"] and not i["worker"]]
    out.sort(key=kanban.sort_key)
    return out[0] if out else None


def reclaim(rows, name):
    """Limpa itens que ficaram com o Worker deste agente após uma interrupção."""
    for i in rows:
        if i["worker"] == name:
            log(f"recuperando item órfão #{i['number']} (Worker={name})")
            kanban.clear_field(i["number"], "Worker")


def _after(result, backoff, short, max_backoff):
    if result == "rate_limited":
        log(f"rate limit sem fallback. aguardando {backoff}s")
        time.sleep(backoff)
        return min(backoff * 2, max_backoff)
    time.sleep(short)
    return short


def loop(config):
    name = config["name"]
    statuses = rules.combined_statuses(config["rules"])
    unblock = any(r.unblock for r in config["rules"])
    interactive = any(r.interactive for r in config["rules"])
    interval = config["interval"]
    short = config.get("short_interval", 30)
    max_backoff = config.get("max_backoff", 3600)
    mention_interval = config.get("mention_interval", interval)

    log(f"iniciado: name={name} statuses={statuses} unblock={unblock} "
        f"interactive={interactive} "
        f"repo={config['repo']} project={config['project']} model={config['model']}")

    backoff = short
    last_mention = 0.0
    while True:
        try:
            rows = kanban.items()
            reclaim(rows, name)

            now = time.time()
            if now - last_mention >= mention_interval:
                last_mention = now
                mention = kanban.next_mention(name)
                if mention:
                    log(f"respondendo menção @{name} em #{mention['number']} ({mention['status']})")
                    result = process(config, name, mention["number"], mention=mention)
                    backoff = _after(result, backoff, short, max_backoff)
                    continue

            if unblock:
                blk = next_blocked(rows)
                if blk:
                    log(f"desbloqueando #{blk['number']} (status={blk['status']})")
                    result = process(config, name, blk["number"], blk["title"])
                    backoff = _after(result, backoff, short, max_backoff)
                    continue

            if interactive:
                task = kanban.next_comment(name, statuses)
            else:
                task = next_task(rows, statuses)
            if task is None:
                log(f"fila vazia para {statuses or 'nada'}. dormindo {interval}s")
                backoff = short
                time.sleep(interval)
                continue

            log(f"reclamando #{task['number']} ({task['status']})")
            result = process(config, name, task["number"], task["title"])
            backoff = _after(result, backoff, short, max_backoff)

        except kanban.RateLimitError as e:
            log(f"rate limit: {e}. aguardando {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

        except Exception as e:  # noqa: BLE001
            log(f"erro no loop: {e}. dormindo {interval}s")
            time.sleep(interval)


def run(config):
    loop(config)
