"""Identitäten 07 (Paket D): MCP-Tool `get_nightly_report` – letzter Bericht des Nachtlaufs (nur lesen).

Vertrag: CONTRACT.md „Nachträge: Identitäten – Mehrfachauswahl, Gesichtsqualität, Nachtlauf“, ID.H/ID.J.
    get_nightly_report(report_id: int | None = None) → Bericht (Text, Zusammenfassung, Aktionen gekürzt auf 30).
Medien-URLs signiert (`signed=1`). Ändert nichts – Anwenden geht nur in der Oberfläche (Anmeldung im Browser).
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import READ_ONLY, BackendError, api, fail, ok

MAX_ACTIONS = 30
TYPE_DE = {"merge": "Zusammenführen", "review": "Rückfrage", "curate": "Galerie kuratieren",
           "threshold": "Schwelle je Person", "cleanup": "Aufräumen"}


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
    t = TYPE_DE.get(a.get("type", ""), a.get("type", ""))
    if a.get("type") in ("merge", "review"):
        s = f"{t}: {a.get('src_display_name')} → {a.get('into_display_name')} ({float(a.get('score') or 0):.2f})"
    elif a.get("type") == "threshold":
        s = f"{t}: {a.get('display_name')} {a.get('old') if a.get('old') is not None else 'global'} → {a.get('new')}"
    else:
        s = f"{t}: {a.get('display_name')}"
    if a.get("skipped_reason"):
        s += f" – übersprungen ({a['skipped_reason']})"
    return s


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def get_nightly_report(ctx: Context, report_id: Optional[int] = None) -> CallToolResult:
        """Bericht des nächtlichen Gesichter-Trainings („Nachtlauf“) von VisionGuard: welche unbenannten
        Gesichts-Fragmente zusammengeführt wurden bzw. im Probelauf würden, welche Rückfragen gestellt, welche
        Galerien kuratiert und welche Schwellen je Person angepasst wurden. Ohne `report_id` der neueste Bericht.
        Nur lesen – Anwenden eines Probelaufs geht nur in der Oberfläche."""
        try:
            rid = report_id
            if rid is None:
                lst = await api(ctx, "/nightly/reports", {"limit": 1})
                if not lst.get("items"):
                    return ok("Es gibt noch keinen Nachtlauf-Bericht.", {"report": None})
                rid = lst["items"][0]["id"]
            r = await api(ctx, f"/nightly/reports/{int(rid)}", {"signed": 1},
                          not_found=f"Nachtlauf-Bericht {rid} nicht gefunden.")
        except BackendError as e:
            return fail(str(e))
        actions = r.get("actions") or []
        data = {"report": {k: r.get(k) for k in ("id", "mode", "trigger", "status", "started_at", "finished_at",
                                                  "error", "applied_at", "applied_report_id", "summary", "text")},
                "actions": [_action(a) for a in actions[:MAX_ACTIONS]], "actions_total": len(actions)}
        kind = "Probelauf" if r.get("mode") == "preview" else "Nachtlauf"
        lines = [f"{kind} {r.get('id')} vom {r.get('started_at')}: {r.get('error') or r.get('text')}"]
        lines += [f"- {_line(a)}" for a in actions[:MAX_ACTIONS]]
        if len(actions) > MAX_ACTIONS:
            lines.append(f"… und {len(actions) - MAX_ACTIONS} weitere Aktionen")
        return ok("\n".join(lines), data)
