"""W3 (A2): MCP-Tool `ask_camera` – Gerüst aus K3.

Vertrag (CONTRACT.md „Nachträge: Welle 3“, W3.H):
    ask_camera(question, camera_id=None, camera_name=None, region=None, allow_cloud=False)
        → POST /api/cameras/{id}/ask (Scope read, 10/min je Key; Header `X-VG-Source: mcp`)
        → {answer, yes_no, engine, model, ms, camera_id, camera_name, snapshot_url} + ImageContent (Standbild)
    Fragen möglichst englisch (Modell versteht Englisch am besten).
Text deutsch, Medien-URLs signiert (`signed=1`), Fehler als fail(...) mit deutscher Meldung.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    """A2: hier die Tools definieren (`@mcp.tool(annotations=READ_ONLY|WRITE)`)."""
    return None
