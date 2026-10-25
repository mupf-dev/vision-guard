#!/usr/bin/env python3
"""Kleiner OpenAI-kompatibler Test-Server für den LLM-Client (W2) – ohne Kosten, nur Standardbibliothek.

Start (z. B. im Docker-Netz des Stacks, dann `llm_base_url = http://llm-mock:8099/v1`):

    docker run --rm -d --name llm-mock --network vision-guard_default -v "$PWD/scripts:/s:ro" \
        python:3.12-slim python /s/llm_mock.py --port 8099

oder lokal `python3 scripts/llm_mock.py --port 8099`. Endpunkte: `POST /v1/chat/completions`, `GET /v1/models`.

Antwortmodi (Umgebungsvariable `LLM_MOCK_MODE` oder Kopfzeile `X-Mock-Mode`):
- `echo` (Standard): wiederholt die letzte Nutzernachricht (max. 400 Zeichen) – gut für Plausibilitätsprüfungen,
  weil keine neuen Fakten entstehen.
- `fixed`: Text aus `LLM_MOCK_TEXT`.
- `invent`: hängt eine erfundene Uhrzeit an („… um 03:17 Uhr“) – testet den Rückfall auf Satzbausteine (B3).
- `error`: HTTP 500; `slow`: wartet `LLM_MOCK_DELAY` Sekunden (Timeout-Test).
Ein Schlüssel wird nicht geprüft und nie ausgegeben.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    server_version = "llm-mock/1.0"

    def log_message(self, fmt, *args):  # knapp, ohne Header (Authorization!)
        print(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}", flush=True)

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            return self._json(200, {"object": "list", "data": [{"id": "mock", "object": "model"}]})
        if self.path == "/health":
            return self._json(200, {"ok": True})
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            return self._json(404, {"error": {"message": "not found"}})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._json(400, {"error": {"message": "invalid json"}})
        mode = (self.headers.get("X-Mock-Mode") or os.environ.get("LLM_MOCK_MODE") or "echo").lower()
        msgs = body.get("messages") or []
        last = next((m.get("content") for m in reversed(msgs) if m.get("role") == "user"), "") or ""
        if mode == "error":
            return self._json(500, {"error": {"message": "mock error"}})
        if mode == "slow":
            time.sleep(float(os.environ.get("LLM_MOCK_DELAY", "120")))
        if mode == "fixed":
            text = os.environ.get("LLM_MOCK_TEXT", "OK")
        elif mode == "invent":
            text = (str(last)[:300] + " Außerdem kam jemand um 03:17 Uhr.").strip()
        else:
            text = str(last)[:400] or "OK"
        self._json(200, {
            "id": f"mock-{int(time.time() * 1000)}", "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model") or "mock",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": sum(len(str(m.get("content") or "")) // 4 for m in msgs),
                      "completion_tokens": len(text) // 4, "total_tokens": 0},
        })


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8099)
    a = ap.parse_args()
    print(f"llm-mock auf {a.host}:{a.port} (Modus {os.environ.get('LLM_MOCK_MODE', 'echo')})", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
