#!/usr/bin/env python3
"""chat-server — HTTP wrapper that answers board-copilot questions with opencode.

Reads configuration from the environment:
  AW_CHAT_TOKEN    bearer token required on POST /reply
  AW_CHAT_PORT     listening port (default 8080)
  AW_MODEL         opencode model (provider/model)
  AW_WORKDIR       working directory passed to opencode
  AW_BACKEND       board backend for reading items (default github; use local)

Endpoints:
  GET  /health  -> {"ok": true}
  POST /reply   -> {"reply": "..."}   body: {"messages": [{"role","body"}, ...]}

The service is read-only: it inspects the board and answers; it never mutates it.
"""
import http.server
import json
import os
import subprocess
import sys

import kanban

PROMPT_HEADER = (
    "You are the Kanban board copilot for a self-hosted board. "
    "Answer the user's question about the board clearly and concisely, in the "
    "same language the user wrote in. You are READ-ONLY: never change the board."
)


def board_snapshot():
    try:
        return kanban.items()
    except Exception:  # noqa: BLE001
        return []


def build_prompt(messages):
    lines = [PROMPT_HEADER, "", "Current board items (JSON):",
             json.dumps(board_snapshot(), ensure_ascii=False), "", "Conversation:"]
    for message in messages:
        lines.append(f"{message.get('role', 'user')}: {message.get('body', '')}")
    lines.append("")
    lines.append("Reply to the last user message now.")

    return "\n".join(lines)


def run_opencode(prompt):
    model = os.environ.get("AW_MODEL", "deepseek/deepseek-v4-pro")
    workdir = os.environ.get("AW_WORKDIR", "/workspace")
    result = subprocess.run(
        ["opencode", "run", "--auto", "-m", model, "--dir", workdir, prompt],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "opencode failed").strip())

    return result.stdout.strip()


def reply_to(payload):
    return run_opencode(build_prompt(payload.get("messages") or []))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/reply":
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        self._reply()

    def _authorized(self):
        token = os.environ.get("AW_CHAT_TOKEN", "")
        return token != "" and self.headers.get("Authorization", "") == f"Bearer {token}"

    def _reply(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(400, {"error": "invalid json"})
            return
        try:
            reply = reply_to(payload)
        except Exception as error:  # noqa: BLE001
            self._json(502, {"error": str(error)})
            return
        self._json(200, {"reply": reply})

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("[chat] " + (fmt % args) + "\n")


def main():
    port = int(os.environ.get("AW_CHAT_PORT", "8080"))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"chat service listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
