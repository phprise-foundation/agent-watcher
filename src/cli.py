"""Entrypoint do agent-watcher: CLI + setup do ambiente + loop."""
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
            raise SystemExit(f"--env inválido (esperado KEY=VALUE): {pair}")
        k, v = pair.split("=", 1)
        os.environ[k] = v


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agent-watcher",
        description="Agente autônomo que opera um quadro Kanban do GitHub (Project V2).",
    )
    ap.add_argument("--name", required=True, help="identidade do agente (assina comentários/Worker)")
    ap.add_argument("--rule", action="append", required=True,
                    help="regra (predefinida ou caminho). Repetível.")
    ap.add_argument("--model", required=True, help="modelo no formato provider/model")
    ap.add_argument("--fallback-model", default=None,
                    help="modelo alternativo (provider/model) usado apenas na iteração atual em caso de rate limit (429/quota)")
    ap.add_argument("--repo", required=True, help="repositório owner/repo")
    ap.add_argument("--project", required=True, help="número do Project V2")
    ap.add_argument("--workdir", required=True, help="diretório de trabalho (montado)")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE. Repetível.")
    ap.add_argument("--rules-dir", default=os.environ.get("AW_RULES_DIR", "/app/rules"),
                    help="diretório das regras predefinidas (default: /app/rules)")
    ap.add_argument("--profile", default=None,
                    help="perfil de personalidade (nome predefinido ou caminho .yaml). Se omitido, usa a personalidade padrão da rule")
    ap.add_argument("--profiles-dir", default=os.environ.get("AW_PROFILES_DIR", "/app/profiles"),
                    help="diretório dos perfis predefinidos (default: /app/profiles)")
    ap.add_argument("--interval", type=int, default=300,
                    help="segundos entre varreduras quando a fila está vazia (default 300)")
    ap.add_argument("--short-interval", type=int, default=30,
                    help="segundos entre tarefas após uma execução (default 30)")
    args = ap.parse_args(argv)

    parse_env(args.env)

    if "/" not in args.repo:
        raise SystemExit(f"--repo inválido (esperado owner/repo): {args.repo}")
    owner, repo = args.repo.split("/", 1)
    os.environ["AW_OWNER"] = owner
    os.environ["AW_REPO"] = args.repo
    os.environ["AW_PROJECT"] = str(args.project)
    os.environ["AW_RULES_DIR"] = args.rules_dir
    os.environ["AW_PROFILES_DIR"] = args.profiles_dir
    os.environ["AW_WORKDIR"] = args.workdir

    if not os.path.isdir(args.workdir):
        raise SystemExit(f"--workdir não existe: {args.workdir}")

    loaded = rules.load_rules(args.rule)
    if not loaded:
        raise SystemExit("nenhuma regra carregada")

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
