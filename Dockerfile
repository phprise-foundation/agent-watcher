# agent-watcher — autonomous Kanban agent (GitHub Project V2) in a container.
# Base: official opencode image (already ships the CLI at /usr/local/bin/opencode).
FROM ghcr.io/anomalyco/opencode:latest

# gh (GitHub CLI), git, bash and python3 (+ yaml) for the helper and the loop.
RUN apk add --no-cache bash git python3 py3-yaml github-cli

WORKDIR /app

COPY src/ /app/src/
COPY rules/ /app/rules/
COPY profiles/ /app/profiles/
COPY bin/agent-watcher /usr/local/bin/agent-watcher
COPY bin/kanban /usr/local/bin/kanban
COPY bin/chat-server /usr/local/bin/chat-server

RUN chmod +x /usr/local/bin/agent-watcher /usr/local/bin/kanban /usr/local/bin/chat-server

ENV PYTHONPATH=/app
ENV AW_RULES_DIR=/app/rules
ENV AW_PROFILES_DIR=/app/profiles

ENTRYPOINT ["agent-watcher"]
