"""W3 (B1): MCP-Tool `search_history` – Gerüst aus K3.

Vertrag (CONTRACT.md „Nachträge: Welle 3“, W3.H):
    search_history(query, since=None, until=None, camera=None, limit=5, include_image=False)
        → {query, items:[{camera_name, room, start_ts, end_ts, best_ts, score, thumb_url}]} aus GET /history/search?signed=1
Text deutsch, Medien-URLs signiert (`signed=1`), Fehler als fail(...) mit deutscher Meldung.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    """B1: hier die Tools definieren (`@mcp.tool(annotations=READ_ONLY|WRITE)`)."""
    return None
