# agent-watcher — agente autônomo de Kanban (GitHub Project V2) em container.
# Base: imagem oficial do opencode (já traz o CLI em /usr/local/bin/opencode).
FROM ghcr.io/anomalyco/opencode:latest

# gh (GitHub CLI), git, bash e python3 (+ yaml) para o helper e o loop.
RUN apk add --no-cache bash git python3 py3-yaml github-cli

WORKDIR /app

COPY src/ /app/src/
COPY rules/ /app/rules/
COPY profiles/ /app/profiles/
COPY bin/agent-watcher /usr/local/bin/agent-watcher
COPY bin/kanban /usr/local/bin/kanban

RUN chmod +x /usr/local/bin/agent-watcher /usr/local/bin/kanban

ENV PYTHONPATH=/app
ENV AW_RULES_DIR=/app/rules
ENV AW_PROFILES_DIR=/app/profiles

ENTRYPOINT ["agent-watcher"]
