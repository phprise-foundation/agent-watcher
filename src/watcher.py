"""agent-watcher loop: scans the board, claims tasks and invokes opencode."""
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
            f"You were mentioned in issue #{mention['number']} ({mention['title']}).\n"
            f"Mention snippet: {mention['mention']}\n"
            f"- Read the issue and comments: gh issue view {mention['number']} --repo {config['repo']} --comments\n"
            f"- Answer every mention of @{name} that has no reply yet, signing with '{name}: '.\n"
            f"- When done, clear the Worker: kanban clear {mention['number']} --field Worker\n"
        )
    else:
        task_section = (
            f"Work on issue #{number} in repository {config['repo']}.\n"
            f"- Read the issue and comments: gh issue view {number} --repo {config['repo']} --comments\n"
            f"- ALWAYS communicate via comment, signing with '{name}: '.\n"
            f"- When done, clear the Worker: kanban clear {number} --field Worker\n"
        )

    return f"""You are "{name}", an autonomous development agent operating on a GitHub Kanban board (Project V2). There is no real-time supervisor: your communication and the state of the work live in the GitHub issues.

# Personality

{personalities or "(not defined)"}

# Your role's rules

{rule_bodies}

# Communication (via issue comments)

- All communication happens in the issue comments.
- ALWAYS write in the same language the issue was opened in (title and body). Detect the user's language and mirror it: if the issue is in Portuguese, reply in Portuguese; if in Korean, reply in Korean; and so on.
- To mention/trigger an agent, write `@AgentName` in a comment.
- Always sign your comments with `{name}: `.
- When working on an issue, read the comments and answer every `@{name}` mention that has no reply from you yet.

# Interacting with the board (helper `kanban`)

- Query: kanban list [--status <x>] [--blocked] [--json]
- Pending mentions: kanban mentions <name>
- Change state: kanban set <n> --status <x> [--role <r>] [--worker <w>] [--blocked|--unblocked] [--blocked-by <task>|--clear-blocked-by]
- Clear field: kanban clear <n> --field Worker
- Comment: kanban comment <n> --text "..."
- Create issue: kanban create --title "..." --body-file <file.md> [--status <x>] [--role <r>] [--blocked-by <task>]

Valid statuses (linear flow, items do NOT go backwards; a fix creates a new issue):
{STATUSES_HELP}

# Current task

{task_section}
"""


def run_agent(config, model, prompt, number=None):
    tag = f" for #{number}" if number is not None else ""
    log(f"invoking opencode{tag} ({model})")
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
    log(f"opencode exited with code {r.returncode}")
    if _llm_rate_limited(err_text):
        return "rate_limited"
    return "error"


def process(config, name, number, title=None, mention=None):
    """Claims the item, runs opencode (with fallback) and releases the Worker."""
    kanban.set_field(number, "Worker", name)
    prompt = build_prompt(config, number=number, title=title, mention=mention)
    result = run_agent(config, config["model"], prompt, number)
    if result == "rate_limited":
        fallback = config.get("fallback_model")
        if fallback:
            log(f"rate limit on primary model; trying fallback {fallback} on #{number}")
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
    """Clears items that kept this agent's Worker after an interruption."""
    for i in rows:
        if i["worker"] == name:
            log(f"recovering orphan item #{i['number']} (Worker={name})")
            kanban.clear_field(i["number"], "Worker")


def _after(result, backoff, short, max_backoff):
    if result == "rate_limited":
        log(f"rate limit with no fallback. waiting {backoff}s")
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

    log(f"started: name={name} statuses={statuses} unblock={unblock} "
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
                    log(f"answering mention @{name} on #{mention['number']} ({mention['status']})")
                    result = process(config, name, mention["number"], mention=mention)
                    backoff = _after(result, backoff, short, max_backoff)
                    continue

            if unblock:
                blk = next_blocked(rows)
                if blk:
                    log(f"unblocking #{blk['number']} (status={blk['status']})")
                    result = process(config, name, blk["number"], blk["title"])
                    backoff = _after(result, backoff, short, max_backoff)
                    continue

            if interactive:
                task = kanban.next_comment(name, statuses)
            else:
                task = next_task(rows, statuses)
            if task is None:
                log(f"queue empty for {statuses or 'nothing'}. sleeping {interval}s")
                backoff = short
                time.sleep(interval)
                continue

            log(f"claiming #{task['number']} ({task['status']})")
            result = process(config, name, task["number"], task["title"])
            backoff = _after(result, backoff, short, max_backoff)

        except kanban.RateLimitError as e:
            log(f"rate limit: {e}. waiting {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

        except Exception as e:  # noqa: BLE001
            log(f"loop error: {e}. sleeping {interval}s")
            time.sleep(interval)


def run(config):
    loop(config)
