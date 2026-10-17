"""agent-watcher entrypoint: CLI + environment setup + loop."""
import argparse
import os
import sys

import profiles
import roles
import watcher


def parse_env(pairs):
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"invalid --env (expected KEY=VALUE): {pair}")
        k, v = pair.split("=", 1)
        os.environ[k] = v


def configure_github(args):
    if not args.repo or "/" not in args.repo:
        raise SystemExit(f"invalid --repo (expected owner/repo): {args.repo}")
    if not args.project:
        raise SystemExit("--project is required for the github backend")
    owner, _ = args.repo.split("/", 1)
    os.environ["AW_OWNER"] = owner
    os.environ["AW_REPO"] = args.repo
    os.environ["AW_PROJECT"] = str(args.project)


def configure_local(args):
    if not args.kanban_url:
        raise SystemExit("--kanban-url is required for the local backend")
    os.environ["AW_KANBAN_URL"] = args.kanban_url
    if args.kanban_token:
        os.environ["AW_KANBAN_TOKEN"] = args.kanban_token


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agent-watcher",
        description="Autonomous agent that operates a Kanban board (GitHub Project V2 or a self-hosted board).",
    )
    ap.add_argument("--name", required=True, help="agent identity (signs comments/Worker)")
    ap.add_argument("--role", action="append", dest="roles", default=[],
                    help="role (predefined name or path). Repeatable.")
    ap.add_argument("--rule", action="append", dest="roles", default=[],
                    help=argparse.SUPPRESS)
    ap.add_argument("--model", required=True, help="model in provider/model format")
    ap.add_argument("--fallback-model", default=None,
                    help="fallback model (provider/model) used only in the current iteration on rate limit (429/quota)")
    ap.add_argument("--backend", choices=["github", "local"], default="github",
                    help="board backend (default: github)")
    ap.add_argument("--repo", default=None, help="owner/repo repository (github backend)")
    ap.add_argument("--project", default=None, help="Project V2 number (github backend)")
    ap.add_argument("--kanban-url", default=os.environ.get("AW_KANBAN_URL"),
                    help="board base URL (local backend)")
    ap.add_argument("--kanban-token", default=os.environ.get("AW_KANBAN_TOKEN"),
                    help="board bearer token (local backend)")
    ap.add_argument("--workdir", required=True, help="working directory (mounted)")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE. Repeatable.")
    ap.add_argument("--roles-dir", default=os.environ.get("AW_ROLES_DIR", "/app/roles"),
                    help="predefined roles directory (default: /app/roles)")
    ap.add_argument("--profile", default=None,
                    help="personality profile (predefined name or .yaml path). If omitted, uses the role's default personality")
    ap.add_argument("--profiles-dir", default=os.environ.get("AW_PROFILES_DIR", "/app/profiles"),
                    help="predefined profiles directory (default: /app/profiles)")
    ap.add_argument("--interval", type=int, default=300,
                    help="seconds between scans when the queue is empty (default 300)")
    ap.add_argument("--short-interval", type=int, default=30,
                    help="seconds between tasks after a run (default 30)")
    ap.add_argument("--runs-file", default=os.environ.get("AW_RUNS_FILE"),
                    help="runs ledger path (JSON lines; default <workdir>/.agent-watcher/runs.jsonl)")
    args = ap.parse_args(argv)

    parse_env(args.env)

    os.environ["AW_BACKEND"] = args.backend
    os.environ["AW_AGENT_NAME"] = args.name
    os.environ["AW_ROLES_DIR"] = args.roles_dir
    os.environ["AW_PROFILES_DIR"] = args.profiles_dir
    os.environ["AW_WORKDIR"] = args.workdir

    if args.backend == "local":
        configure_local(args)
    else:
        configure_github(args)

    if not os.path.isdir(args.workdir):
        raise SystemExit(f"--workdir does not exist: {args.workdir}")

    if not args.roles:
        raise SystemExit("--role is required")

    loaded = roles.load_roles(args.roles)
    if not loaded:
        raise SystemExit("no role loaded")

    role = loaded[0].name

    profile_personality = None
    if args.profile:
        profile_personality = profiles.render(profiles.load_profile(args.profile))

    config = {
        "name": args.name,
        "role": role,
        "backend": args.backend,
        "model": args.model,
        "fallback_model": args.fallback_model,
        "repo": args.repo,
        "project": str(args.project) if args.project else "",
        "kanban_url": args.kanban_url,
        "workdir": args.workdir,
        "roles": loaded,
        "profile_personality": profile_personality,
        "interval": args.interval,
        "short_interval": args.short_interval,
        "runs_file": args.runs_file,
    }

    watcher.run(config)


if __name__ == "__main__":
    main()
