"""W3 (A2): MCP-Tool `ask_camera` – Frage an das aktuelle Bild einer Kamera.

Vertrag (CONTRACT.md „Nachträge: Welle 3“, W3.H):
    ask_camera(question, camera_id=None, camera_name=None, region=None, allow_cloud=False)
        → POST /api/cameras/{id}/ask (Scope read, 10/min je Key; Header `X-VG-Source: mcp`)
        → {answer, yes_no, engine, model, ms, camera_id, camera_name, snapshot_url} + ImageContent (Standbild)
Ohne `allow_cloud` fragt das Tool nur das lokale Modell (`engine:"local"`); mit `allow_cloud` darf das Backend auf
die Cloud ausweichen (`engine:"auto"`), sofern die Kamera dafür freigegeben ist.
"""

from __future__ import annotations

import base64
from typing import Optional

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent

from .cameras import SNAPSHOT_MAX_WIDTH, _shrink_jpeg
from .common import (AUTH_ERROR, READ_ONLY, BackendError, _auth_header, _detail, api, cameras, fail, find_camera,
                     http, log)

ASK_TIMEOUT_S = 60.0
YES_NO_DE = {"yes": "ja", "no": "nein", "unknown": "unklar"}


async def _post_ask(ctx: Context, cam_id: int, body: dict) -> dict:
    try:
        resp = await http().post(f"/api/cameras/{cam_id}/ask", json=body, timeout=ASK_TIMEOUT_S,
                                 headers={"Authorization": _auth_header(ctx), "X-VG-Source": "mcp"})
    except httpx.HTTPError as e:
        log.warning("Backend nicht erreichbar (ask): %s", type(e).__name__)
        raise BackendError("VisionGuard-Backend ist nicht erreichbar.") from e
    if resp.status_code == 401:
        raise BackendError(AUTH_ERROR)
    if resp.status_code in (403, 404, 409, 429, 502, 503):
        detail = _detail(resp)
        if resp.status_code == 403 and "Cloud" not in detail:
            raise BackendError(AUTH_ERROR)
        if resp.status_code == 429 and detail.startswith("Zu viele"):
            detail += " – bitte etwas warten."
        raise BackendError(detail + ("." if not detail.endswith((".", "!", "?")) else ""))
    if resp.status_code >= 400:
        raise BackendError(f"Backend-Fehler: {_detail(resp)}")
    return resp.json()


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def ask_camera(ctx: Context, question: str, camera_id: Optional[int] = None,
                         camera_name: Optional[str] = None, region: Optional[list[float]] = None,
                         allow_cloud: bool = False) -> CallToolResult:
        """Stellt eine Frage zum AKTUELLEN Bild einer Kamera und beantwortet sie mit einem lokalen Bildmodell,
        z. B. "Is the light on?", "Is the window open?", "Is there a person on the sofa?". Bitte die Frage auf
        ENGLISCH stellen (das lokale Modell versteht Englisch am besten); Ja/Nein-Fragen liefern zusätzlich
        yes_no. Kamera per camera_id oder camera_name (auch Raumname). Optional region = [x1, y1, x2, y2]
        (normiert 0..1) für einen Bildausschnitt. allow_cloud=true erlaubt den Cloud-Fallback, falls die Kamera
        dafür freigegeben ist. Höchstens 10 Fragen pro Minute; Antwort dauert meist 1–5 s."""
        q = (question or "").strip()
        if not q:
            return fail("Bitte eine Frage angeben.")
        if len(q) > 300:
            return fail("Die Frage ist zu lang (max. 300 Zeichen).")
        if region is not None:
            if len(region) != 4 or not all(0.0 <= float(v) <= 1.0 for v in region) \
                    or region[2] <= region[0] or region[3] <= region[1]:
                return fail("region muss [x1, y1, x2, y2] mit Werten 0..1 sein (x2 > x1, y2 > y1).")
        try:
            cam = find_camera(await cameras(ctx), camera_id, camera_name)
            res = await _post_ask(ctx, int(cam["id"]), {"question": q, "region": region,
                                                        "engine": "auto" if allow_cloud else "local"})
            structured = {"answer": res.get("answer"), "yes_no": res.get("yes_no"), "engine": res.get("engine"),
                          "model": res.get("model"), "ms": res.get("ms"), "camera_id": res.get("camera_id"),
                          "camera_name": res.get("camera_name"), "snapshot_url": None}
            content: list = []
            snap = res.get("snapshot_url")
            if snap:
                try:
                    signed = await api(ctx, "/ask", {"camera_id": cam["id"], "limit": 1, "signed": 1})
                    item = next((x for x in signed.get("items") or [] if x.get("id") == res.get("id")), None)
                    structured["snapshot_url"] = (item or {}).get("snapshot_url")
                except BackendError:
                    pass
                try:
                    img = await http().get(snap, headers={"Authorization": _auth_header(ctx)}, timeout=20)
                    if img.status_code == 200 and img.content[:2] == b"\xff\xd8":
                        data = _shrink_jpeg(img.content, SNAPSHOT_MAX_WIDTH)
                        content.append(ImageContent(type="image", data=base64.b64encode(data).decode(),
                                                    mime_type="image/jpeg"))
                except httpx.HTTPError:
                    pass
            where = f"Kamera {res.get('camera_name') or cam.get('name')}"
            engine = "Cloud" if res.get("engine") == "cloud" else "lokal"
            yn = res.get("yes_no") or "unknown"
            text = (f"{where}, Frage „{q}“: {res.get('answer') or '(keine Antwort)'}"
                    + (f" [Ja/Nein: {YES_NO_DE.get(yn, yn)}]" if yn != "unknown" else "")
                    + f" ({engine}, {res.get('model')}, {round(float(res.get('ms') or 0))} ms)")
            content.append(TextContent(type="text", text=text))
            return CallToolResult(content=content, structured_content=structured)
        except BackendError as e:
            return fail(str(e))
