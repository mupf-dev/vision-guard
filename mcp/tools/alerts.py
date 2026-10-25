"""W2 (C1/D1): MCP-Tools `list_alerts`, `acknowledge_alert` – Gerüst aus K2.

Vertrag (CONTRACT.md „Nachträge: Welle 2“, W2.H):
    list_alerts(active_only: bool = True, limit: int = 10) → {total, items:[{event_id, camera_id, camera_name, label, posture, since, acknowledged_at, repeat, snapshot_url}]}
    acknowledge_alert(event_id: str) → {event_id, acknowledged_at}   (write, POST /events/{id}/ack)
Text deutsch, Medien-URLs signiert (`signed=1`), Fehler als fail(...) mit deutscher Meldung.
Schreibende Tools: `annotations=WRITE`, zuerst `await require_write(ctx)`, Aufruf über `api_send(ctx, "POST", …)`.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    """C1: hier die Tools definieren (`@mcp.tool(annotations=READ_ONLY|WRITE)`)."""
    return None
