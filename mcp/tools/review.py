"""W2 (D2): MCP-Tools `list_open_questions`, `answer_question` – Gerüst aus K2.

Vertrag (CONTRACT.md „Nachträge: Welle 2“, W2.H):
    list_open_questions(limit: int = 10) → {open_count, items:[{id, kind, question, camera_name, crop_url, created_at, expires_at}]}
    answer_question(id: int, answer: "yes"|"no"|"skip", correct_label: str | None = None) → ReviewRequest (write; Body source="mcp")
Text deutsch, Medien-URLs signiert (`signed=1`), Fehler als fail(...) mit deutscher Meldung.
Schreibende Tools: `annotations=WRITE`, zuerst `await require_write(ctx)`, Aufruf über `api_send(ctx, "POST", …)`.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    """D2: hier die Tools definieren (`@mcp.tool(annotations=READ_ONLY|WRITE)`)."""
    return None
