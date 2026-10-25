"""Kleiner Selbsttest für den VisionGuard-MCP-Dienst.

Aufruf (API-Key aus Administration → API-Zugänge, nie ins Repo schreiben):

    VG_API_KEY=vgt_… python selftest.py [URL] [Suchbegriff]

URL Standard `http://localhost:8100/mcp` (im Container) bzw. von außen `http://localhost:8080/mcp`.
Prüft: ohne Key → 401, list_tools, list_cameras, find_object, list_visible_objects, who_is_where,
what_changed (W1 B2).
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

EXPECTED = {"find_object", "list_visible_objects", "get_object_history", "list_cameras",
            "get_camera_snapshot", "who_is_where"}
EXPECTED |= {"what_changed"}   # W1 B2


def _text(result) -> str:
    return "\n".join(getattr(c, "text", f"<{c.type}>") for c in result.content)


async def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("VG_MCP_URL", "http://localhost:8100/mcp")
    query = sys.argv[2] if len(sys.argv) > 2 else "Fernbedienung"
    key = os.environ.get("VG_API_KEY", "").strip()
    if not key:
        print("Bitte VG_API_KEY setzen (API-Key aus Administration → API-Zugänge).")
        return 2

    # 1) ohne Key muss der Dienst 401 liefern
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                         headers={"Accept": "application/json, text/event-stream"})
    print(f"Ohne Key: HTTP {r.status_code} (erwartet 401)")
    if r.status_code != 401:
        return 1

    # 2) mit Key: MCP-Client
    http_client = create_mcp_http_client(headers={"Authorization": f"Bearer {key}"})
    async with http_client, Client(streamable_http_client(url, http_client=http_client)) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        print("Tools:", ", ".join(sorted(names)))
        missing = EXPECTED - names
        if missing:
            print("FEHLER – es fehlen:", ", ".join(sorted(missing)))
            return 1
        failed = 0
        for name, args in (("list_cameras", {}), ("find_object", {"query": query}),
                           ("list_visible_objects", {}), ("who_is_where", {})):
            res = await client.call_tool(name, args)
            print(f"\n== {name} {args} {'(Fehler)' if res.is_error else ''}\n{_text(res)}")
            failed += bool(res.is_error)
        # W1 B2: Veränderungen der letzten Stunde (Medien-URLs signiert)
        res = await client.call_tool("what_changed", {"since": "1h"})
        print(f"\n== what_changed {{'since': '1h'}} {'(Fehler)' if res.is_error else ''}\n{_text(res)}")
        failed += bool(res.is_error)
    print("\nSelbsttest", "mit Fehlern" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
