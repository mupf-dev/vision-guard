"""Identitäten 07 (Paket D): MCP-Tool `get_nightly_report` – letzter Bericht des Nachtlaufs (nur lesen).

Vertrag: CONTRACT.md „Nachträge: Identitäten – Mehrfachauswahl, Gesichtsqualität, Nachtlauf“, ID.H/ID.J.
    get_nightly_report(report_id: int | None = None) → Bericht (Text, Zusammenfassung, Aktionen gekürzt auf 30).
Medien-URLs signiert (`signed=1`). Ändert nichts – Anwenden geht nur in der Oberfläche (Anmeldung im Browser).
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import READ_ONLY, BackendError, api, fail, fmt_datetime, mapped, ok, t

MAX_ACTIONS = 30


def _action(a: dict[str, Any]) -> dict[str, Any]:
    keep = ("idx", "type", "applied", "skipped_reason", "src_id", "src_display_name", "src_cover_url", "into_id",
            "into_display_name", "into_cover_url", "into_named", "score", "second_score", "samples", "events",
            "identity_id", "display_name", "old", "new")
    out = {k: a.get(k) for k in keep if a.get(k) is not None}
    if a.get("type") == "curate":
        out["negatives"] = len(a.get("negative_sample_ids") or [])
        out["removed"] = len(a.get("removed_sample_ids") or [])
    return out


def _line(a: dict[str, Any]) -> str:
    kind = mapped("nightly.type", a.get("type", ""))
    if a.get("type") in ("merge", "review"):
        s = f"{kind}: {a.get('src_display_name')} → {a.get('into_display_name')} ({float(a.get('score') or 0):.2f})"
    elif a.get("type") == "threshold":
        old = a.get("old") if a.get("old") is not None else t("nightly.global")
        s = f"{kind}: {a.get('display_name')} {old} → {a.get('new')}"
    else:
        s = f"{kind}: {a.get('display_name')}"
    if a.get("skipped_reason"):
        s += " – " + t("nightly.skipped", reason=a["skipped_reason"])
    return s


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def get_nightly_report(ctx: Context, report_id: Optional[int] = None) -> CallToolResult:
        """Report of VisionGuard's nightly face training ("nightly run"): which unnamed face fragments were merged
        (or would be, in a preview run), which questions were asked, which galleries were curated and which
        per-person thresholds were adjusted. Without `report_id` the newest report.
        Read only – applying a preview run is only possible in the web interface."""
        try:
            rid = report_id
            if rid is None:
                lst = await api(ctx, "/nightly/reports", {"limit": 1})
                if not lst.get("items"):
                    return ok(t("nightly.none"), {"report": None})
                rid = lst["items"][0]["id"]
            r = await api(ctx, f"/nightly/reports/{int(rid)}", {"signed": 1},
                          not_found=t("nightly.not_found", id=rid))
        except BackendError as e:
            return fail(str(e))
        actions = r.get("actions") or []
        data = {"report": {k: r.get(k) for k in ("id", "mode", "trigger", "status", "started_at", "finished_at",
                                                  "error", "applied_at", "applied_report_id", "summary", "text")},
                "actions": [_action(a) for a in actions[:MAX_ACTIONS]], "actions_total": len(actions)}
        kind = t("nightly.preview") if r.get("mode") == "preview" else t("nightly.run")
        lines = [t("nightly.head", kind=kind, id=r.get("id"), time=fmt_datetime(r.get("started_at")),
                   text=r.get("error") or r.get("text"))]
        lines += [f"- {_line(a)}" for a in actions[:MAX_ACTIONS]]
        if len(actions) > MAX_ACTIONS:
            lines.append(t("nightly.more", n=len(actions) - MAX_ACTIONS))
        return ok("\n".join(lines), data)
