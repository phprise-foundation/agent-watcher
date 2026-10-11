"""agent-watcher entrypoint: CLI + environment setup + loop."""
import argparse
import os
import sys

import kanban
import profiles
import rules
import watcher


def parse_env(pairs):
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"invalid --env (expected KEY=VALUE): {pair}")
        k, v = pair.split("=", 1)
        os.environ[k] = v


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agent-watcher",
        description="Autonomous agent that operates a GitHub Kanban board (Project V2).",
    )
    ap.add_argument("--name", required=True, help="agent identity (signs comments/Worker)")
    ap.add_argument("--rule", action="append", required=True,
                    help="rule (predefined or path). Repeatable.")
    ap.add_argument("--model", required=True, help="model in provider/model format")
    ap.add_argument("--fallback-model", default=None,
                    help="fallback model (provider/model) used only in the current iteration on rate limit (429/quota)")
    ap.add_argument("--repo", required=True, help="owner/repo repository")
    ap.add_argument("--project", required=True, help="Project V2 number")
    ap.add_argument("--workdir", required=True, help="working directory (mounted)")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE. Repeatable.")
    ap.add_argument("--rules-dir", default=os.environ.get("AW_RULES_DIR", "/app/rules"),
                    help="predefined rules directory (default: /app/rules)")
    ap.add_argument("--profile", default=None,
                    help="personality profile (predefined name or .yaml path). If omitted, uses the rule's default personality")
    ap.add_argument("--profiles-dir", default=os.environ.get("AW_PROFILES_DIR", "/app/profiles"),
                    help="predefined profiles directory (default: /app/profiles)")
    ap.add_argument("--interval", type=int, default=300,
                    help="seconds between scans when the queue is empty (default 300)")
    ap.add_argument("--short-interval", type=int, default=30,
                    help="seconds between tasks after a run (default 30)")
    args = ap.parse_args(argv)

    parse_env(args.env)

    if "/" not in args.repo:
        raise SystemExit(f"invalid --repo (expected owner/repo): {args.repo}")
    owner, repo = args.repo.split("/", 1)
    os.environ["AW_OWNER"] = owner
    os.environ["AW_REPO"] = args.repo
    os.environ["AW_PROJECT"] = str(args.project)
    os.environ["AW_RULES_DIR"] = args.rules_dir
    os.environ["AW_PROFILES_DIR"] = args.profiles_dir
    os.environ["AW_WORKDIR"] = args.workdir

    if not os.path.isdir(args.workdir):
        raise SystemExit(f"--workdir does not exist: {args.workdir}")

    loaded = rules.load_rules(args.rule)
    if not loaded:
        raise SystemExit("no rule loaded")

    profile_personality = None
    if args.profile:
        profile_personality = profiles.render(profiles.load_profile(args.profile))

    config = {
        "name": args.name,
        "model": args.model,
        "fallback_model": args.fallback_model,
        "repo": args.repo,
        "project": str(args.project),
        "workdir": args.workdir,
        "rules": loaded,
        "profile_personality": profile_personality,
        "interval": args.interval,
        "short_interval": args.short_interval,
    }

    watcher.run(config)


if __name__ == "__main__":
    main()
