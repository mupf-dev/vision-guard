"""W3 (A3+C4): MCP-Tools `list_sensors`, `get_sensor`, `get_shopping_list`, `add_to_shopping_list` – Gerüst aus K3.

Vertrag (CONTRACT.md „Nachträge: Welle 3“, W3.H):
    list_sensors(camera_id=None, room=None, supply=None) → {total, items:[…]} aus GET /sensors?signed=1
    get_sensor(sensor_id=None, name=None) → VirtualSensor + history (≤ 20)
    get_shopping_list(include_done=False) → {open_count, items} aus GET /shopping-list
    add_to_shopping_list(name, note=None) (write, require_write) → POST /shopping-list
Text deutsch, Medien-URLs signiert (`signed=1`), Fehler als fail(...) mit deutscher Meldung.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    """A3+C4: hier die Tools definieren (`@mcp.tool(annotations=READ_ONLY|WRITE)`)."""
    return None
