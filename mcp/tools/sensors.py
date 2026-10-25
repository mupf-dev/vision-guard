"""W3 (A3+C4): MCP-Tools `list_sensors`, `get_sensor`, `get_shopping_list`, `add_to_shopping_list`.

Vertrag (CONTRACT.md „Nachträge: Welle 3“, W3.H):
    list_sensors(camera_id=None, room=None, supply=None) → {total, items:[{id, name, kind, camera_name, room, state,
        state_text, value, since}]} aus GET /sensors?signed=1
    get_sensor(sensor_id=None, name=None) → VirtualSensor (kompakt) + history (≤ 20)
    get_shopping_list(include_done=False) → {open_count, items:[{id, name, note, source, created_at, done}]}
    add_to_shopping_list(name, note=None) (write) → ShoppingItem (Quelle `mcp`)
"""

from __future__ import annotations

from typing import Optional

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult

from .common import (AUTH_ERROR, READ_ONLY, WRITE, WRITE_ERROR, BackendError, _auth_header, _detail, api, fail,
                     fmt_ago, fmt_time, http, n_de, ok, require_write)

STATE_DE = {"on": "an", "off": "aus", "ok": "ok", "low": "knapp", "out": "leer", "unknown": "unklar"}
KIND_DE = {"brightness": "Helligkeit", "blink": "Blinken", "reference": "Referenzbilder",
           "inventory_count": "Gegenstände zählen", "fill": "Füllstand", "vlm": "Bildmodell"}
SOURCE_DE = {"auto": "automatisch", "manual": "von Hand", "mcp": "per KI-Assistent"}


def _compact(s: dict) -> dict:
    return {"id": s.get("id"), "name": s.get("name"), "kind": s.get("kind"), "camera_id": s.get("camera_id"),
            "camera_name": s.get("camera_name"), "room": s.get("room"), "state": s.get("state"),
            "state_text": s.get("state_text"), "value": s.get("value"), "since": s.get("since"),
            "supply": s.get("supply"), "enabled": s.get("enabled"), "snapshot_url": s.get("snapshot_url")}


def _line(s: dict) -> str:
    place = s.get("room") or s.get("camera_name") or ""
    text = s.get("state_text") or STATE_DE.get(s.get("state") or "", s.get("state") or "")
    return f"- {s.get('name')} ({place}{', Vorrat' if s.get('supply') else ''}): {text}"


def _shop(i: dict) -> dict:
    return {"id": i.get("id"), "name": i.get("name"), "note": i.get("note") or "", "source": i.get("source"),
            "sensor_id": i.get("sensor_id"), "created_at": i.get("created_at"), "done": bool(i.get("done"))}


async def _post_shopping(ctx: Context, body: dict) -> dict:
    """POST /shopping-list mit dem Key des Clients und `X-VG-Source: mcp` (Quelle „mcp“)."""
    try:
        resp = await http().post("/api/shopping-list", json=body,
                                 headers={"Authorization": _auth_header(ctx), "X-VG-Source": "mcp"})
    except httpx.HTTPError as e:
        raise BackendError("VisionGuard-Backend ist nicht erreichbar.") from e
    if resp.status_code == 401:
        raise BackendError(AUTH_ERROR)
    if resp.status_code == 403:
        raise BackendError(WRITE_ERROR if "write" in _detail(resp) else AUTH_ERROR)
    if resp.status_code >= 400:
        raise BackendError(f"Backend-Fehler: {_detail(resp)}")
    data = resp.json()
    data["_created"] = resp.status_code == 201
    return data


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_sensors(ctx: Context, camera_id: Optional[int] = None, room: Optional[str] = None,
                           supply: Optional[bool] = None) -> CallToolResult:
        """Virtuelle Sensoren (Bildbereiche als Schalter: Licht an/aus, Fenster offen/zu, Vorrat ok/knapp/leer)
        mit aktuellem Zustand. Filter: camera_id, room (Raumname), supply=true nur Vorräte."""
        try:
            items = await api(ctx, "/sensors", {"camera_id": camera_id, "room": room,
                                                "supply": None if supply is None else str(bool(supply)).lower(),
                                                "signed": 1})
            data = [_compact(s) for s in items]
            if not data:
                return ok("Keine virtuellen Sensoren gefunden.", {"total": 0, "items": []})
            return ok(f"{n_de(len(data), 'Sensor', 'Sensoren')}:\n" + "\n".join(_line(s) for s in data),
                      {"total": len(data), "items": data})
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_sensor(ctx: Context, sensor_id: Optional[int] = None, name: Optional[str] = None) -> CallToolResult:
        """Einen virtuellen Sensor mit Zustand und Verlauf (letzte 20 Wechsel/Messungen). Per sensor_id oder Name
        (ohne Groß-/Kleinschreibung, auch Teilname)."""
        try:
            if sensor_id is None:
                if not (name or "").strip():
                    return fail("Bitte sensor_id oder name angeben.")
                key = name.strip().casefold()
                all_s = await api(ctx, "/sensors", {"signed": 1})
                hits = [s for s in all_s if (s.get("name") or "").casefold() == key] or \
                       [s for s in all_s if key in (s.get("name") or "").casefold()]
                if not hits:
                    return fail(f"Kein Sensor „{name}“ gefunden.")
                if len(hits) > 1:
                    return fail("Mehrere Sensoren passen: " + ", ".join(f"{s['name']} (id {s['id']})" for s in hits))
                sensor_id = int(hits[0]["id"])
            s = await api(ctx, f"/sensors/{sensor_id}", {"signed": 1}, not_found=f"Sensor {sensor_id} nicht gefunden.")
            hist = await api(ctx, f"/sensors/{sensor_id}/history", {"limit": 20, "signed": 1})
            data = {**_compact(s), "kind_de": KIND_DE.get(s.get("kind") or "", s.get("kind")),
                    "last_eval": s.get("last_eval"), "last_engine": s.get("last_engine"),
                    "history": [{"ts": h.get("ts"), "state": h.get("state"), "value": h.get("value"),
                                 "engine": h.get("engine"), "snapshot_url": h.get("snapshot_url")} for h in hist]}
            changes = []
            prev = None
            for h in hist:
                if h.get("state") != prev:
                    changes.append(f"{fmt_time(h.get('ts'))}: {STATE_DE.get(h.get('state') or '', h.get('state'))}")
                    prev = h.get("state")
            text = (f"Sensor {s.get('name')} ({data['kind_de']}, {s.get('room') or s.get('camera_name')}): "
                    f"{s.get('state_text') or STATE_DE.get(s.get('state') or '', '')}"
                    + (f", zuletzt geprüft {fmt_ago(s.get('last_eval'))}" if s.get("last_eval") else "") + ".")
            if changes:
                text += "\nVerlauf (neueste zuerst): " + "; ".join(changes[:10])
            return ok(text, data)
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_shopping_list(ctx: Context, include_done: bool = False) -> CallToolResult:
        """Einkaufsliste (automatisch aus knappen Vorräten und von Hand ergänzt). include_done=true zeigt auch
        erledigte Einträge."""
        try:
            res = await api(ctx, "/shopping-list", {"done": "all" if include_done else "false"})
            items = [_shop(i) for i in res.get("items") or []]
            open_count = int(res.get("open_count") or 0)
            if not items:
                return ok("Die Einkaufsliste ist leer.", {"open_count": 0, "items": []})
            lines = []
            for i in items:
                extra = ", ".join(x for x in (i["note"], SOURCE_DE.get(i["source"] or "", "")) if x)
                lines.append(f"- {'[erledigt] ' if i['done'] else ''}{i['name']}{f' ({extra})' if extra else ''}")
            return ok(f"Einkaufsliste – {n_de(open_count, 'offener Eintrag', 'offene Einträge')}:\n" + "\n".join(lines),
                      {"open_count": open_count, "items": items})
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=WRITE)
    async def add_to_shopping_list(ctx: Context, name: str, note: Optional[str] = None) -> CallToolResult:
        """Artikel auf die Einkaufsliste setzen (braucht einen API-Key mit Schreibrecht). Steht der Artikel schon
        offen auf der Liste, bleibt es bei einem Eintrag."""
        try:
            name = " ".join((name or "").split())
            if not name:
                return fail("Bitte einen Artikel angeben.")
            if len(name) > 100 or len(note or "") > 300:
                return fail("Name höchstens 100, Notiz höchstens 300 Zeichen.")
            await require_write(ctx)
            item = await _post_shopping(ctx, {"name": name, "note": note})
            created = item.pop("_created", True)
            data = _shop(item)
            text = (f"„{data['name']}“ steht jetzt auf der Einkaufsliste." if created
                    else f"„{data['name']}“ stand schon auf der Einkaufsliste.")
            return ok(text, data)
        except BackendError as e:
            return fail(str(e))
