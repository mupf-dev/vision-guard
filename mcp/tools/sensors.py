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

from .common import (READ_ONLY, WRITE, BackendError, _auth_header, _detail, api, auth_error, fail, fmt_ago, fmt_time,
                     http, mapped, ok, require_write, t, unreachable_error, write_error)


def _compact(s: dict) -> dict:
    return {"id": s.get("id"), "name": s.get("name"), "kind": s.get("kind"), "camera_id": s.get("camera_id"),
            "camera_name": s.get("camera_name"), "room": s.get("room"), "state": s.get("state"),
            "state_text": s.get("state_text"), "value": s.get("value"), "since": s.get("since"),
            "supply": s.get("supply"), "enabled": s.get("enabled"), "snapshot_url": s.get("snapshot_url"),
            # C4-VLM (W4): Messart des Bildmodells (yesno|count|fill) und letzte Modellantwort
            "measure": _measure(s), "last_answer": s.get("last_answer")}


def _measure(s: dict) -> Optional[str]:
    if s.get("kind") == "vlm":
        return str((s.get("params") or {}).get("measure") or "yesno")
    if s.get("kind") == "inventory_count":
        return "count"
    if s.get("kind") == "fill":
        return "fill"
    return None


def _line(s: dict) -> str:
    place = s.get("room") or s.get("camera_name") or ""
    text = s.get("state_text") or mapped("sensors.state", s.get("state"))
    if s.get("value") is not None and s.get("state") != "unknown" and (s.get("measure") or _measure(s)) == "count":
        text += " " + t("sensors.pieces", n=round(float(s["value"])))
    return f"- {s.get('name')} ({place}{t('sensors.supply_suffix') if s.get('supply') else ''}): {text}"


def _shop(i: dict) -> dict:
    return {"id": i.get("id"), "name": i.get("name"), "note": i.get("note") or "", "source": i.get("source"),
            "sensor_id": i.get("sensor_id"), "created_at": i.get("created_at"), "done": bool(i.get("done"))}


async def _post_shopping(ctx: Context, body: dict) -> dict:
    """POST /shopping-list mit dem Key des Clients und `X-VG-Source: mcp` (Quelle „mcp“)."""
    try:
        resp = await http().post("/api/shopping-list", json=body,
                                 headers={"Authorization": _auth_header(ctx), "X-VG-Source": "mcp"})
    except httpx.HTTPError as e:
        raise BackendError(unreachable_error()) from e
    if resp.status_code == 401:
        raise BackendError(auth_error(), 401)
    if resp.status_code == 403:
        raise BackendError(write_error() if "write" in _detail(resp) else auth_error(), 403)
    if resp.status_code >= 400:
        raise BackendError(t("common.backend_error", detail=_detail(resp)), resp.status_code)
    data = resp.json()
    data["_created"] = resp.status_code == 201
    return data


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_sensors(ctx: Context, camera_id: Optional[int] = None, room: Optional[str] = None,
                           supply: Optional[bool] = None) -> CallToolResult:
        """Virtual sensors (image regions acting as switches: light on/off, window open/closed, supply
        ok/low/out) with their current state. Filters: camera_id, room (room name), supply=true only supplies."""
        try:
            items = await api(ctx, "/sensors", {"camera_id": camera_id, "room": room,
                                                "supply": None if supply is None else str(bool(supply)).lower(),
                                                "signed": 1})
            data = [_compact(s) for s in items]
            if not data:
                return ok(t("sensors.none"), {"total": 0, "items": []})
            return ok(t("sensors.n_sensors", n=len(data)) + "\n" + "\n".join(_line(s) for s in data),
                      {"total": len(data), "items": data})
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_sensor(ctx: Context, sensor_id: Optional[int] = None, name: Optional[str] = None) -> CallToolResult:
        """One virtual sensor with state and history (last 20 changes/measurements). By sensor_id or name
        (case-insensitive, partial names work too)."""
        try:
            if sensor_id is None:
                if not (name or "").strip():
                    return fail(t("sensors.id_required"))
                key = name.strip().casefold()
                all_s = await api(ctx, "/sensors", {"signed": 1})
                hits = [s for s in all_s if (s.get("name") or "").casefold() == key] or \
                       [s for s in all_s if key in (s.get("name") or "").casefold()]
                if not hits:
                    return fail(t("sensors.not_found_name", name=name))
                if len(hits) > 1:
                    return fail(t("sensors.ambiguous", names=", ".join(f"{s['name']} (id {s['id']})" for s in hits)))
                sensor_id = int(hits[0]["id"])
            s = await api(ctx, f"/sensors/{sensor_id}", {"signed": 1}, not_found=t("sensors.not_found_id", id=sensor_id))
            hist = await api(ctx, f"/sensors/{sensor_id}/history", {"limit": 20, "signed": 1})
            data = {**_compact(s), "kind_de": mapped("sensors.kind", s.get("kind")),   # Feldname bleibt (Vertrag), Text in der Antwortsprache
                    "last_eval": s.get("last_eval"), "last_engine": s.get("last_engine"),
                    "history": [{"ts": h.get("ts"), "state": h.get("state"), "value": h.get("value"),
                                 "engine": h.get("engine"), "snapshot_url": h.get("snapshot_url")} for h in hist]}
            changes = []
            prev = None
            for h in hist:
                if h.get("state") != prev:
                    changes.append(f"{fmt_time(h.get('ts'))}: {mapped('sensors.state', h.get('state'))}")
                    prev = h.get("state")
            text = (t("sensors.head", name=s.get("name"), kind=data["kind_de"],
                      place=s.get("room") or s.get("camera_name"),
                      state=s.get("state_text") or mapped("sensors.state", s.get("state"), ""))
                    + (", " + t("sensors.last_checked", ago=fmt_ago(s.get("last_eval"))) if s.get("last_eval") else "")
                    + ".")
            if s.get("last_answer"):
                text += "\n" + t("sensors.last_answer", answer=s["last_answer"])
            if changes:
                text += "\n" + t("sensors.history", changes="; ".join(changes[:10]))
            return ok(text, data)
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=READ_ONLY)
    async def get_shopping_list(ctx: Context, include_done: bool = False) -> CallToolResult:
        """Shopping list (filled automatically from low supplies and by hand). include_done=true also shows
        completed entries."""
        try:
            res = await api(ctx, "/shopping-list", {"done": "all" if include_done else "false"})
            items = [_shop(i) for i in res.get("items") or []]
            open_count = int(res.get("open_count") or 0)
            if not items:
                return ok(t("sensors.shopping_empty"), {"open_count": 0, "items": []})
            lines = []
            for i in items:
                extra = ", ".join(x for x in (i["note"], mapped("sensors.shopping_source", i["source"], "")) if x)
                lines.append(f"- {t('sensors.done_prefix') if i['done'] else ''}{i['name']}{f' ({extra})' if extra else ''}")
            return ok(t("sensors.shopping_head", n=open_count) + "\n" + "\n".join(lines),
                      {"open_count": open_count, "items": items})
        except BackendError as e:
            return fail(str(e))

    @mcp.tool(annotations=WRITE)
    async def add_to_shopping_list(ctx: Context, name: str, note: Optional[str] = None) -> CallToolResult:
        """Put an item on the shopping list (requires an API key with write permission). If the item is already
        open on the list, it stays a single entry."""
        try:
            name = " ".join((name or "").split())
            if not name:
                return fail(t("sensors.item_required"))
            if len(name) > 100 or len(note or "") > 300:
                return fail(t("sensors.item_too_long"))
            await require_write(ctx)
            item = await _post_shopping(ctx, {"name": name, "note": note})
            created = item.pop("_created", True)
            data = _shop(item)
            text = (t("sensors.added", name=data["name"]) if created
                    else t("sensors.already_listed", name=data["name"]))
            return ok(text, data)
        except BackendError as e:
            return fail(str(e))
