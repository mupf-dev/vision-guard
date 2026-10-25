"""W2 (D2): MCP-Tools `list_open_questions`, `answer_question`.

Vertrag (CONTRACT.md „Nachträge: Welle 2“, W2.H):
    list_open_questions(limit: int = 10) → {open_count, items:[{id, kind, question, camera_name, crop_url, created_at, expires_at}]}
    answer_question(id: int, answer: "yes"|"no"|"skip", correct_label: str | None = None) → ReviewRequest (write; Body source="mcp")
Text deutsch, Medien-URLs signiert (`signed=1`), Fehler als fail(...) mit deutscher Meldung.
"""

from __future__ import annotations

from typing import Literal, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import READ_ONLY, WRITE, BackendError, api, api_send, fail, fmt_time, n_de, ok, require_write

_KIND_DE = {"inventory_label": "Gegenstand", "inventory_identity": "eigener Gegenstand",
            "identity_match": "Person/Tier"}
_ANSWER_DE = {"yes": "Ja", "no": "Nein", "skip": "Überspringen"}


def _compact(r: dict) -> dict:
    return {"id": r.get("id"), "kind": r.get("kind"), "question": r.get("question"),
            "camera_name": r.get("camera_name"), "crop_url": r.get("crop_url"),
            "created_at": r.get("created_at"), "expires_at": r.get("expires_at")}


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_open_questions(ctx: Context, limit: int = 10) -> CallToolResult:
        """Offene Rückfragen der Erkennung („Bitte bestätigen“), z. B. „Erkannt als Fernbedienung – stimmt das?“.
        Jede Frage hat ein Bild (crop_url) und lässt sich mit answer_question beantworten."""
        try:
            limit = max(1, min(int(limit), 50))
            data = await api(ctx, "/review", {"status": "open", "limit": limit, "signed": 1})
            items = [_compact(r) for r in data.get("items", [])]
            open_count = int(data.get("open_count", len(items)))
            if not items:
                text = "Keine offenen Rückfragen."
            else:
                lines = [f"{n_de(open_count, 'offene Rückfrage', 'offene Rückfragen')} "
                         f"(heute {data.get('asked_today', '?')}/{data.get('max_per_day', '?')} gefragt):"]
                for c in items:
                    where = f", Kamera {c['camera_name']}" if c.get("camera_name") else ""
                    lines.append(f"- [id {c['id']}] {c['question']} ({_KIND_DE.get(c['kind'], c['kind'])}{where}, "
                                 f"seit {fmt_time(c['created_at'])})" + (f" Bild: {c['crop_url']}" if c.get("crop_url") else ""))
                text = "\n".join(lines)
            return ok(text, {"open_count": open_count, "items": items})
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=WRITE)
    async def answer_question(id: int, answer: Literal["yes", "no", "skip"], ctx: Context,
                              correct_label: Optional[str] = None) -> CallToolResult:
        """Beantwortet eine Rückfrage (id aus list_open_questions) mit "yes", "no" oder "skip". Bei "no" zu einem
        Gegenstand kann correct_label (Vokabular-Schlüssel, z. B. "remote") das richtige Label nennen.
        Braucht einen API-Key mit Schreibrecht."""
        try:
            await require_write(ctx)
            if answer not in _ANSWER_DE:
                return fail("Antwort muss yes, no oder skip sein.")
            body = {"answer": answer, "source": "mcp"}
            if correct_label:
                body["correct_label"] = correct_label.strip()
            r = await api_send(ctx, "POST", f"/review/{int(id)}/answer", json=body,
                               not_found=f"Rückfrage {id} ist unbekannt.")
            c = _compact(r)
            c.update({"status": r.get("status"), "answered_at": r.get("answered_at"),
                      "answer_source": r.get("answer_source"), "correct_label": r.get("correct_label")})
            text = f"Gespeichert: „{r.get('question')}“ – {_ANSWER_DE[answer]}."
            return ok(text, c)
        except BackendError as e:
            msg = str(e)
            if "nicht mehr offen" in msg or "abgelaufen" in msg:
                msg = f"Rückfrage {id} ist nicht mehr offen (schon beantwortet oder abgelaufen)."
            return fail(msg)
