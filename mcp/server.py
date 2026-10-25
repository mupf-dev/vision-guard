"""VisionGuard MCP-Dienst (Streamable HTTP unter /mcp).

Der Dienst hat keinen eigenen Zugang: Er verlangt vom MCP-Client einen API-Key (`Authorization: Bearer vgt_…`),
prüft ihn über `GET /api/auth/me` und reicht denselben Header bei jedem Backend-Aufruf durch. Keys werden nie
geloggt. Lesende Tools brauchen Scope `read`; Tools mit Wirkung (ab W2) prüfen `write` selbst
(`tools.common.require_write`). Siehe CONTRACT.md, Abschnitt „I. MCP-Dienst“.
Die Tools liegen je Thema unter `tools/` (Aufteilung seit Welle 1, siehe `tools/__init__.py`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Optional

import httpx
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from tools import register_all
from tools.common import AUTH_ERROR, http

log = logging.getLogger("vg.mcp")

PORT = int(os.environ.get("VG_MCP_PORT", "8100"))
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("VG_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
AUTH_CACHE_S = 30.0

# ------------------------------------------------------------------ MCP-Server

mcp = MCPServer(
    "VisionGuard",
    instructions=(
        "VisionGuard ist eine private Videoanalyse für Heimkameras. Die Tools beantworten, wo Gegenstände "
        "(z. B. Schlüssel, Fernbedienung, Buch) zuletzt gesehen wurden, welche Gegenstände gerade sichtbar sind, "
        "wer bzw. welches Tier gerade wo ist, und liefern aktuelle Kamerabilder. Lagebeziehungen („auf dem Tisch“) "
        "sind 2D-Schätzungen und deshalb mit „vermutlich“ formuliert. Bild-URLs sind signiert und zeitlich begrenzt."
    ),
    log_level="WARNING",
)
register_all(mcp)


# ------------------------------------------------------------------ HTTP-App mit Auth


class BearerAuth:
    """ASGI-Wrapper: prüft den Bearer-Key vor jeder MCP-Anfrage; /health bleibt offen."""

    def __init__(self, app):
        self.app = app
        self._cache: dict[str, float] = {}  # sha256(key) → gültig bis

    async def _send_json(self, send, status: int, body: dict, extra: Optional[list] = None) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        headers = [(b"content-type", b"application/json; charset=utf-8"),
                   (b"content-length", str(len(raw)).encode())] + (extra or [])
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": raw})

    async def _check(self, auth: str) -> int:
        digest = hashlib.sha256(auth.encode()).hexdigest()
        now = time.monotonic()
        if self._cache.get(digest, 0) > now:
            return 200
        try:
            resp = await http().get("/api/auth/me", headers={"Authorization": auth})
        except httpx.HTTPError:
            return 503
        if resp.status_code == 200:
            if len(self._cache) > 1000:
                self._cache = {k: v for k, v in self._cache.items() if v > now}
            self._cache[digest] = now + AUTH_CACHE_S
            return 200
        self._cache.pop(digest, None)
        return 401 if resp.status_code in (401, 403) else 503

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("path") == "/health":
            return await self._send_json(send, 200, {"status": "ok"})
        auth = ""
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                auth = v.decode("latin-1").strip()
                break
        challenge = [(b"www-authenticate", b'Bearer realm="VisionGuard MCP"')]
        if not auth.lower().startswith("bearer ") or len(auth) < 12:
            return await self._send_json(send, 401, {"detail": "API-Key fehlt (Authorization: Bearer vgt_…)"},
                                         challenge)
        status = await self._check(auth)
        if status == 401:
            return await self._send_json(send, 401, {"detail": AUTH_ERROR}, challenge)
        if status != 200:
            return await self._send_json(send, 503, {"detail": "VisionGuard-Backend ist nicht erreichbar."})
        return await self.app(scope, receive, send)


def build_app():
    if ALLOWED_HOSTS:
        origins = [f"{p}://{h}" for h in ALLOWED_HOSTS for p in ("http", "https")]
        security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                             allowed_hosts=ALLOWED_HOSTS, allowed_origins=origins)
    else:
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    inner = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True,
                                    transport_security=security, host="0.0.0.0")
    return BearerAuth(inner)


app = build_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # access_log aus: Zugriffslogs enthalten zwar keine Header, aber wir halten die Ausgabe knapp
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning", access_log=False,
                proxy_headers=True, forwarded_allow_ips="*")
