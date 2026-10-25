"""W1 (B2): MCP-Tool `what_changed` – Gerüst aus K1.

Vertrag (CONTRACT.md „Nachträge: Welle 1“):
    what_changed(since: str = "today", until: str | None = None, camera_id: int | None = None, camera_name: str | None = None, room: str | None = None, limit: int = 30) → InventoryChanges aus GET /inventory/changes?signed=1
Text deutsch, Medien-URLs signiert, Fehler als fail(...) mit deutscher Meldung.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    """B2: hier `@mcp.tool(annotations=READ_ONLY) async def what_changed(...)` definieren."""
    return None
