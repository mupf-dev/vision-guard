"""W1 (A1): MCP-Tool `list_notifications` – Gerüst aus K1.

Vertrag (CONTRACT.md „Nachträge: Welle 1“):
    list_notifications(since: str | None = None, limit: int = 10, source: str | None = None) → {total, items:[{id, ts, source, level, rule, title, message, camera_id, image_url}]} aus GET /notifications?signed=1
Text deutsch, Medien-URLs signiert, Fehler als fail(...) mit deutscher Meldung.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    """A1: hier `@mcp.tool(annotations=READ_ONLY) async def list_notifications(...)` definieren."""
    return None
