"""W2 (C3): MCP-Tool `get_pet_diary` – Gerüst aus K2.

Vertrag (CONTRACT.md „Nachträge: Welle 2“, W2.H):
    get_pet_diary(pet: str | None = None, days: int = 1) → {pets:[{name, label, purposes}], visits:[…]} aus /pets/summary + /pets/visits?signed=1
Text deutsch, Medien-URLs signiert (`signed=1`), Fehler als fail(...) mit deutscher Meldung.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    """C3: hier die Tools definieren (`@mcp.tool(annotations=READ_ONLY|WRITE)`)."""
    return None
