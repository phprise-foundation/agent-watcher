# agent-watcher — autonomous Kanban agent (GitHub Project V2) in a container.
# Base: official opencode image (already ships the CLI at /usr/local/bin/opencode).
FROM ghcr.io/anomalyco/opencode:latest

# gh (GitHub CLI), git, bash and python3 (+ yaml) for the helper and the loop.
RUN apk add --no-cache bash git python3 py3-yaml github-cli

WORKDIR /app

COPY src/ /app/src/
COPY roles/ /app/roles/
COPY profiles/ /app/profiles/
COPY bin/agent-watcher /usr/local/bin/agent-watcher
COPY bin/kanban /usr/local/bin/kanban
COPY bin/chat-server /usr/local/bin/chat-server
COPY bin/agent-runner /usr/local/bin/agent-runner

RUN chmod +x /usr/local/bin/agent-watcher /usr/local/bin/kanban /usr/local/bin/chat-server /usr/local/bin/agent-runner

ENV PYTHONPATH=/app
ENV AW_ROLES_DIR=/app/roles
ENV AW_PROFILES_DIR=/app/profiles

ENTRYPOINT ["agent-watcher"]
