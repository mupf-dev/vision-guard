"""W2 (B3): MCP-Tool `get_daily_report` – Gerüst aus K2.

Vertrag (CONTRACT.md „Nachträge: Welle 2“, W2.H):
    get_daily_report(date: str | None = None) → Report (Text + Zustellstatus); ohne gespeicherten Bericht Vorschau über /reports/preview
Text deutsch, Medien-URLs signiert (`signed=1`), Fehler als fail(...) mit deutscher Meldung.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    """B3: hier die Tools definieren (`@mcp.tool(annotations=READ_ONLY|WRITE)`)."""
    return None
