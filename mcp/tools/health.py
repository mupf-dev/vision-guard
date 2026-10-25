"""W1 (C5): MCP-Tool `get_camera_health` – Gerüst aus K1.

Vertrag (CONTRACT.md „Nachträge: Welle 1“):
    get_camera_health(camera_id: int | None = None, camera_name: str | None = None) → {cameras:[{id, name, room, enabled, state, problems:[{type, since, text}]}]} aus GET /cameras bzw. /cameras/{id}/health
Text deutsch, Medien-URLs signiert, Fehler als fail(...) mit deutscher Meldung.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    """C5: hier `@mcp.tool(annotations=READ_ONLY) async def get_camera_health(...)` definieren."""
    return None
