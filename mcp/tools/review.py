"""W2 (D2): MCP-Tools `list_open_questions`, `answer_question`.

Vertrag (CONTRACT.md „Nachträge: Welle 2“, W2.H):
    list_open_questions(limit: int = 10) → {open_count, items:[{id, kind, question, camera_name, crop_url, created_at, expires_at}]}
    answer_question(id: int, answer: "yes"|"no"|"skip", correct_label: str | None = None) → ReviewRequest (write; Body source="mcp")
Text in der Systemsprache, Medien-URLs signiert (`signed=1`), Fehler als fail(...).
"""

from __future__ import annotations

from typing import Literal, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import READ_ONLY, WRITE, BackendError, api, api_send, fail, fmt_time, mapped, ok, require_write, t

_ANSWERS = ("yes", "no", "skip")


def _compact(r: dict) -> dict:
    return {"id": r.get("id"), "kind": r.get("kind"), "question": r.get("question"),
            "camera_name": r.get("camera_name"), "crop_url": r.get("crop_url"),
            "created_at": r.get("created_at"), "expires_at": r.get("expires_at")}


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_open_questions(ctx: Context, limit: int = 10) -> CallToolResult:
        """Open questions from the recognition ("please confirm"), e.g. "Detected as remote control – is that
        right?". Each question has an image (crop_url) and can be answered with answer_question."""
        try:
            limit = max(1, min(int(limit), 50))
            data = await api(ctx, "/review", {"status": "open", "limit": limit, "signed": 1})
            items = [_compact(r) for r in data.get("items", [])]
            open_count = int(data.get("open_count", len(items)))
            if not items:
                text = t("review.none")
            else:
                lines = [t("review.n_open", n=open_count, asked=data.get("asked_today", "?"),
                           max=data.get("max_per_day", "?"))]
                for c in items:
                    where = ", " + t("common.camera", name=c["camera_name"]) if c.get("camera_name") else ""
                    lines.append(f"- [id {c['id']}] {c['question']} ({mapped('review.kind', c['kind'])}{where}, "
                                 + t("common.since", time=fmt_time(c["created_at"])) + ")"
                                 + (" " + t("common.image", url=c["crop_url"]) if c.get("crop_url") else ""))
                text = "\n".join(lines)
            return ok(text, {"open_count": open_count, "items": items})
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=WRITE)
    async def answer_question(id: int, answer: Literal["yes", "no", "skip"], ctx: Context,
                              correct_label: Optional[str] = None) -> CallToolResult:
        """Answer a question (id from list_open_questions) with "yes", "no" or "skip". With "no" for an object,
        correct_label (vocabulary key, e.g. "remote") can name the correct label.
        Requires an API key with write permission."""
        try:
            await require_write(ctx)
            if answer not in _ANSWERS:
                return fail(t("review.bad_answer"))
            body = {"answer": answer, "source": "mcp"}
            if correct_label:
                body["correct_label"] = correct_label.strip()
            r = await api_send(ctx, "POST", f"/review/{int(id)}/answer", json=body,
                               not_found=t("review.unknown", id=id))
            c = _compact(r)
            c.update({"status": r.get("status"), "answered_at": r.get("answered_at"),
                      "answer_source": r.get("answer_source"), "correct_label": r.get("correct_label")})
            text = t("review.saved", question=r.get("question"), answer=mapped("review.answer", answer))
            return ok(text, c)
        except BackendError as e:
            msg = str(e)
            if e.status == 409:
                msg = t("review.not_open", id=id)
            return fail(msg)
