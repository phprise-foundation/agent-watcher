"""Loop do agent-watcher: varre o quadro, reclama tarefas e invoca o opencode."""
import datetime
import subprocess
import sys
import time

import kanban
import rules

STATUSES_HELP = "to-do, qa-refinement, coding, reviewing, testing, done"


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def build_prompt(config, number, title):
    rule_bodies = "\n\n".join(r.body for r in config["rules"])
    return f"""Você é "{config['name']}", um agente autônomo de desenvolvimento que atua em um quadro Kanban do GitHub (Project V2). Não há supervisor em tempo real: sua comunicação e o estado do trabalho vivem nas issues do GitHub.

# Regras do seu papel

{rule_bodies}

# Interação com o quadro (helper `kanban`)

- Consultar: kanban list [--status <x>] [--blocked] [--json]
- Mudar estado: kanban set <n> --status <x> [--role <r>] [--worker <w>] [--blocked|--unblocked]
- Limpar campo: kanban clear <n> --field Worker
- Comentar: kanban comment <n> --text "..."
- Criar issue: kanban create --title "..." --body-file <arquivo.md> [--status <x>] [--role <r>]

Statuses válidos (fluxo linear, os itens NÃO voltam; correção vira nova issue):
{STATUSES_HELP}

# Tarefa atual

Trabalhe na issue #{number} do repositório {config['repo']}.
- Leia a issue: gh issue view {number} --repo {config['repo']}
- Comunique-se SEMPRE via comentário, assinando com '{config['name']}: ': gh issue comment {number} --repo {config['repo']} --body '{config['name']}: ...'
- Ao finalizar, limpe o Worker: kanban clear {number} --field Worker
"""


def run_agent(config, number, title):
    prompt = build_prompt(config, number, title)
    log(f"invocando opencode para #{number} ({config['model']})")
    r = subprocess.run(
        ["opencode", "run", "--auto", "--print-logs", "-m", config["model"],
         "--dir", config["workdir"], prompt],
    )
    if r.returncode != 0:
        log(f"opencode terminou com código {r.returncode}")


def next_task(statuses):
    rows = [i for i in kanban.items()
            if i["status"] in statuses and not i["worker"] and not i["blocked"]]
    rows.sort(key=lambda i: (statuses.index(i["status"]) if i["status"] in statuses else 99,
                             kanban.sort_key(i)))
    return rows[0] if rows else None


def next_blocked():
    rows = [i for i in kanban.items() if i["blocked"] and not i["worker"]]
    rows.sort(key=kanban.sort_key)
    return rows[0] if rows else None


def loop(config):
    name = config["name"]
    statuses = rules.combined_statuses(config["rules"])
    unblock = any(r.unblock for r in config["rules"])
    interval = config["interval"]
    short = config.get("short_interval", 10)

    log(f"iniciado: name={name} statuses={statuses} unblock={unblock} "
        f"repo={config['repo']} project={config['project']} model={config['model']}")

    while True:
        try:
            if unblock:
                blk = next_blocked()
                if blk:
                    log(f"desbloqueando #{blk['number']} (status={blk['status']})")
                    kanban.set_field(blk["number"], "Worker", name)
                    run_agent(config, blk["number"], blk["title"])
                    kanban.clear_field(blk["number"], "Worker")
                    time.sleep(short)
                    continue

            task = next_task(statuses)
            if task is None:
                log(f"fila vazia para {statuses or 'nada'}. dormindo {interval}s")
                time.sleep(interval)
                continue

            log(f"reclamando #{task['number']} ({task['status']})")
            kanban.set_field(task["number"], "Worker", name)
            run_agent(config, task["number"], task["title"])
            kanban.clear_field(task["number"], "Worker")
            time.sleep(short)

        except Exception as e:  # noqa: BLE001
            log(f"erro no loop: {e}")
            time.sleep(interval)


def run(config):
    loop(config)
