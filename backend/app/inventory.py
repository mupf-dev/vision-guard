"""Gegenstandsgedächtnis (Inventar).

Übernimmt den vollen Inventar-Zustand des Workers aus `vg/internal/inventory/{camera_id}` (CONTRACT.md, Teil G),
schreibt `InventoryItem`/`InventoryHistory`, erkennt veraltete Kameras (`stale`), berechnet die Lagebeziehung
(„vermutlich auf dem Tisch“) und sendet WebSocket-Nachrichten `{"type":"inventory",...}` (Teil H).

Gegenstände erzeugen bewusst **keine Events** (keine Regeln, keine Event-Flut durch Möbel).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import inventory_names as names
from . import registry, settings_store
from .db import SessionLocal
from .models import Camera, InventoryHistory, InventoryItem
from .schemas import iso, media_url
from .util import from_unix
from .ws import hub

log = logging.getLogger("vg.inventory")

HISTORY_PRESENT_EVERY_S = 300.0   # bei unverändertem „present“ höchstens 1 Historien-Eintrag je 5 min
HISTORY_MOVED_MIN_S = 60.0        # verschobene Gegenstände: frühestens nach 1 min erneut
WS_UPDATE_MIN_S = 10.0            # WS „update“ je Gegenstand höchstens 1×/10 s
MOVED_WS = 0.03                   # Mittelpunktverschiebung für WS-„update“
MOVED_HISTORY = 0.10              # Mittelpunktverschiebung für einen Historien-Eintrag („moved“ laut Worker)
STALE_FACTOR = 3.0                # keine Nachricht seit > 3 × interval_s → stale
MATCH_IOU = 0.3
MATCH_CENTER = 0.05
ON_OVERLAP = 0.6
NEAR_DIST = 0.10
STAND_TOL = 0.03          # Fußpunkt bis so weit über der Oberkante der Fläche zählt als „auf“
STAND_BAND = 0.35        # … bzw. im oberen Drittel der Flächen-Box


# ---------------------------------------------------------------- Geometrie


def _bbox(v: Any) -> Optional[list[float]]:
    try:
        b = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    if len(b) != 4:
        return None
    x1, y1, x2, y2 = b
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]


def _area(b: list[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a: list[float], b: list[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _iou(a: list[float], b: list[float]) -> float:
    i = _inter(a, b)
    u = _area(a) + _area(b) - i
    return i / u if u > 0 else 0.0


def _center(b: list[float]) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _center_dist(a: list[float], b: list[float]) -> float:
    (ax, ay), (bx, by) = _center(a), _center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _box_dist(a: list[float], b: list[float]) -> float:
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return (dx * dx + dy * dy) ** 0.5


def compute_relation(item_bbox: list[float], surfaces: list[tuple[int, str, list[float]]],
                     vocab: dict[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
    """2D-Heuristik: `on` (unterer Mittelpunkt in der Fläche, ≥ 60 % Überlappung – oder Fußpunkt an der Oberkante
    der Fläche, für hohe Dinge wie Lampen; kleinste Fläche gewinnt),
    sonst `near` (nächste Fläche, Abstand < 0,10), sonst None. surfaces = [(item_id, label, bbox)]."""
    a = _area(item_bbox)
    if a <= 0 or not surfaces:
        return None
    bx = (item_bbox[0] + item_bbox[2]) / 2
    by = item_bbox[3]
    on: list[tuple[float, int, str]] = []
    for sid, slabel, sb in surfaces:
        inside = sb[1] <= by <= sb[3] and _inter(item_bbox, sb) / a >= ON_OVERLAP
        # hohe Dinge (Lampe, Pflanze, Flasche) ragen über die Fläche hinaus: Fußpunkt an der Oberkante der Fläche
        standing = sb[1] - STAND_TOL <= by <= sb[1] + STAND_BAND * (sb[3] - sb[1])
        if sb[0] <= bx <= sb[2] and (inside or standing):
            on.append((_area(sb), sid, slabel))
    if on:
        on.sort()
        _, sid, slabel = on[0]
        rtype = "on"
    else:
        near = sorted((_box_dist(item_bbox, sb), sid, slabel) for sid, slabel, sb in surfaces)
        if not near or near[0][0] >= NEAR_DIST:
            return None
        _, sid, slabel = near[0]
        rtype = "near"
    sname = names.display_name(slabel, vocab)
    return {"type": rtype, "item_id": sid, "label": slabel, "name_de": sname,
            "text": names.relation_text(rtype, slabel, sname)}


# ---------------------------------------------------------------- Zeit-/Text-Helfer


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def ago_text(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    dt = _utc(dt)
    if dt is None:
        return "unbekannt"
    now = now or datetime.now(timezone.utc)
    s = max(0.0, (now - dt).total_seconds())
    if s < 60:
        return "gerade eben"
    if s < 3600:
        return f"vor {int(s // 60)} min"
    if s < 86400:
        h = int(s // 3600)
        return f"vor {h} h"
    d = int(s // 86400)
    return "vor 1 Tag" if d == 1 else f"vor {d} Tagen"


def local_time_text(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    """„14:02“ (heute), „gestern 14:02“ oder „22.10. 14:02“ in lokaler Zeit."""
    from .config import config
    dt = _utc(dt)
    if dt is None:
        return "?"
    now = now or datetime.now(timezone.utc)
    loc, nloc = dt.astimezone(config.tz), now.astimezone(config.tz)
    hm = loc.strftime("%H:%M")
    if loc.date() == nloc.date():
        return hm
    if loc.date() == (nloc - timedelta(days=1)).date():
        return f"gestern {hm}"
    return f"{loc.strftime('%d.%m.')} {hm}"


def status_text(state: str, last_seen: Optional[datetime]) -> str:
    if state == "present":
        return "aktuell sichtbar"
    if state == "stale":
        return f"Stand unklar (Kamera liefert gerade keine Gegenstandsanalyse), zuletzt gesehen {ago_text(last_seen)}"
    return f"zuletzt gesehen {ago_text(last_seen)}"


def place_text(cam: dict[str, Any], zone_names: list[str]) -> str:
    """„in Wohnzimmer (Kamera Wohnzimmer, Zone Tischchen)“."""
    extra = [f"Kamera {cam.get('name') or '?'}"]
    if zone_names:
        extra.append(("Zone " if len(zone_names) == 1 else "Zonen ") + ", ".join(zone_names))
    room = cam.get("room")
    if room:
        return f"in {room} ({', '.join(extra)})"
    return f"bei {', '.join(extra)}"


# ---------------------------------------------------------------- Serialisierung


def _media(path: Optional[str], signed: bool) -> Optional[str]:
    if not path:
        return None
    if signed:
        try:
            from .media_sign import sign_media_path  # vom Auth-/Medien-Teil bereitgestellt
            ttl = int(settings_store.get().get("media_link_ttl_h") or 24) * 3600
            return sign_media_path(path, ttl)
        except Exception:  # noqa: BLE001 – Modul fehlt noch o. ä.: unsignierte URL liefern
            log.debug("Signieren der Medien-URL nicht möglich", exc_info=True)
    return media_url(path)


def camera_info(camera_id: int) -> dict[str, Any]:
    cam = registry.get(camera_id) or {}
    return {"id": camera_id, "name": cam.get("name") or f"Kamera {camera_id}", "room": cam.get("room"),
            "zones": cam.get("zones") or {}}


def item_json(it: InventoryItem, *, signed: bool = False, vocab: Optional[dict[str, dict[str, Any]]] = None,
              cam: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if vocab is None:
        vocab = names.vocab_map(settings_store.get().get("inventory_vocabulary") or [])
    cam = cam or camera_info(it.camera_id)
    zmap = cam.get("zones") or {}
    zones = list(it.zones or [])
    zone_names = [zmap.get(z) or z for z in zones]
    return {
        "id": it.id,
        "camera_id": it.camera_id,
        "camera_name": cam.get("name"),
        "room": cam.get("room"),
        "label": it.label,
        "name_de": names.display_name(it.label, vocab),
        "score": round(float(it.score or 0), 4),
        "bbox": [round(float(x), 4) for x in (it.bbox or [])],
        "zones": zones,
        "zone_names": zone_names,
        "state": it.state,
        "visible": it.state == "present",
        "relation": it.relation if it.relation else None,
        "first_seen": iso(it.first_seen),
        "last_seen": iso(it.last_seen),
        "updated_at": iso(it.updated_at),
        "crop_url": _media(it.crop_path, signed),
        "snapshot_url": _media(it.snapshot_path, signed),
        # Zusatz für Menschen/KI-Agenten: „aktuell sichtbar“ / „zuletzt gesehen vor 2 h“
        "status_text": status_text(it.state, it.last_seen),
    }


def history_json(h: InventoryHistory, signed: bool = False) -> dict[str, Any]:
    return {"ts": iso(h.ts), "state": h.state, "camera_id": h.camera_id,
            "bbox": [round(float(x), 4) for x in (h.bbox or [])], "zones": list(h.zones or []),
            "score": round(float(h.score or 0), 4), "crop_url": _media(h.crop_path, signed)}


def present_since(s: Session, it: InventoryItem) -> Optional[datetime]:
    """Seit wann ist der Gegenstand ununterbrochen sichtbar? (aus der Historie, sonst first_seen)"""
    last_other = s.scalar(select(func.max(InventoryHistory.ts))
                          .where(InventoryHistory.item_id == it.id, InventoryHistory.state != "present"))
    q = select(func.min(InventoryHistory.ts)).where(InventoryHistory.item_id == it.id,
                                                    InventoryHistory.state == "present")
    if last_other is not None:
        q = q.where(InventoryHistory.ts > last_other)
    return s.scalar(q) or it.first_seen


def answer_sentence(s: Session, items: list[InventoryItem], query: str, matched_label: Optional[str],
                    vocab: dict[str, dict[str, Any]]) -> str:
    """Deutscher Antwortsatz für die Suche bzw. das MCP-Tool find_object."""
    if not matched_label:
        return f"Kein Gegenstand „{query.strip()}“ bekannt."
    name = names.display_name(matched_label, vocab)
    if not items:
        return f"{name}: bisher von keiner Kamera gesehen."
    top = items[0]
    cam = camera_info(top.camera_id)
    zmap = cam.get("zones") or {}
    place = place_text(cam, [zmap.get(z) or z for z in (top.zones or [])])
    rel = (top.relation or {}).get("text")
    tname = names.display_name(top.label, vocab)
    if top.state == "present":
        since = present_since(s, top)
        parts = [f"{tname}: aktuell sichtbar {place}"]
        if rel:
            parts.append(rel)
        parts.append(f"seit {local_time_text(since)}")
        text = ", ".join(parts)
    else:
        when = f"{ago_text(top.last_seen)} ({local_time_text(top.last_seen)})"
        if top.state == "stale":
            text = f"{tname}: Stand unklar, die Kamera liefert gerade keine Gegenstandsanalyse; zuletzt gesehen {when} {place}"
        else:
            text = f"{tname}: zuletzt gesehen {when} {place}"
        if rel:
            text += f", {rel}"
    others = len(items) - 1
    if others > 0:
        visible_others = sum(1 for i in items[1:] if i.state == "present")
        text += f". Weitere Funde: {others}" + (f" (davon {visible_others} sichtbar)" if visible_others else "")
    return text + "."


# ---------------------------------------------------------------- Laufzeitzustand


class _CamRuntime:
    __slots__ = ("session", "session_msgs", "last_rx", "interval_s", "stopped", "stale")

    def __init__(self) -> None:
        self.session: Optional[str] = None
        self.session_msgs = 0
        self.last_rx = 0.0
        self.interval_s = 3.0
        self.stopped = False
        self.stale = False


class InventoryMemory:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.cams: dict[int, _CamRuntime] = {}
        self.last_history: dict[int, tuple[float, list[float]]] = {}   # item_id -> (zeit, bbox)
        self.last_ws: dict[int, tuple[float, list[float], Optional[str], Optional[str]]] = {}  # bbox, crop, rel
        self.suppressed: set[tuple[int, str, str]] = set()   # (camera_id, session, worker_item_id) nach DELETE

    # ------------------------------------------------------------ Start

    def startup(self) -> None:
        """Kameras mit sichtbaren Gegenständen beobachten: kommt nichts, werden sie nach 3 × Intervall stale."""
        now = time.time()
        with SessionLocal() as s, self.lock:
            rows = s.execute(select(InventoryItem.camera_id, func.max(InventoryItem.worker_session))
                             .where(InventoryItem.state == "present").group_by(InventoryItem.camera_id)).all()
            intervals = dict(s.execute(select(Camera.id, Camera.inventory_interval_s)).all())
            for cam_id, sess in rows:
                rt = self.cams.setdefault(cam_id, _CamRuntime())
                if not rt.last_rx:
                    rt.last_rx = now
                    rt.session = sess
                    rt.session_msgs = 10 ** 6   # bekannte Session: keine Schonfrist
                    rt.interval_s = float(intervals.get(cam_id) or 3)

    # ------------------------------------------------------------ MQTT

    def handle(self, camera_id: int, msg: dict[str, Any]) -> None:
        with self.lock:
            self._handle(camera_id, msg)

    def _handle(self, camera_id: int, msg: dict[str, Any]) -> None:
        st = settings_store.get()
        vocab = names.vocab_map(st.get("inventory_vocabulary") or [])
        miss_m = int(st.get("inventory_miss_m") or 3)
        now_wall = time.time()
        try:
            msg_ts = float(msg.get("ts") or now_wall)
        except (TypeError, ValueError):
            msg_ts = now_wall
        session = str(msg.get("session") or "")
        stopped = bool(msg.get("stopped"))
        try:
            interval = max(0.5, float(msg.get("interval_s") or 3))
        except (TypeError, ValueError):
            interval = 3.0

        rt = self.cams.setdefault(camera_id, _CamRuntime())
        if not stopped and now_wall - msg_ts > STALE_FACTOR * interval:
            # veraltete (z. B. retained) Nachricht eines nicht mehr laufenden Workers: nichts übernehmen,
            # sichtbare Gegenstände gelten als veraltet
            rt.last_rx, rt.interval_s, rt.stopped, rt.stale = msg_ts, interval, False, True
            self._mark_stale(camera_id)
            return
        with SessionLocal() as s:
            rows = list(s.scalars(select(InventoryItem).where(InventoryItem.camera_id == camera_id)).all())
            if session and session != rt.session:
                # neue Session des Workers oder Backend-Neustart: bekannte Session aus der DB übernehmen
                known = any(r.worker_session == session for r in rows)
                rt.session = session
                rt.session_msgs = 10 ** 6 if known else 0
            rt.session_msgs += 1
            rt.last_rx = min(now_wall, msg_ts)  # alte (retained) Nachricht → wird vom tick() schnell stale
            rt.interval_s = interval
            rt.stopped = stopped
            rt.stale = False

            now = datetime.now(timezone.utc)
            prev = {r.id: (r.state, list(r.bbox or []), r.crop_path,
                           (r.relation or {}).get("text")) for r in rows}
            changes = {str(c.get("item_id")): c.get("type") for c in (msg.get("changes") or [])
                       if isinstance(c, dict)}
            snapshot_path = msg.get("snapshot_path") or None
            touched: set[int] = set()
            moved: set[int] = set()

            if stopped:
                for r in rows:
                    if r.state == "present":
                        r.state = "stale"
                        r.updated_at = now
            else:
                items = [i for i in (msg.get("items") or []) if isinstance(i, dict)]
                msg_ids = {str(i.get("item_id")) for i in items if i.get("item_id")}
                by_wid = {r.worker_item_id: r for r in rows if r.worker_session == session and r.worker_item_id}
                claimed: set[int] = set()
                # zuerst sichere Zuordnungen (item_id), dann Label + Geometrie
                ordered = sorted(items, key=lambda i: 0 if str(i.get("item_id")) in by_wid else 1)
                for it in ordered:
                    wst = it.get("state")
                    if wst not in ("present", "missing"):
                        continue  # candidate: Backend speichert nichts
                    wid = str(it.get("item_id") or "")
                    label = str(it.get("label") or "")
                    bbox = _bbox(it.get("bbox"))
                    if not label or bbox is None:
                        continue
                    if (camera_id, session, wid) in self.suppressed:
                        continue
                    row = by_wid.get(wid)
                    if row is not None and row.id in claimed:
                        row = None
                    if row is None:
                        row = self._match_geom(rows, label, bbox, claimed, msg_ids, session)
                    if row is None:
                        if wst != "present":
                            continue
                        row = InventoryItem(camera_id=camera_id, label=label,
                                            first_seen=from_unix(it.get("first_seen")) or now,
                                            state="present", bbox=bbox)
                        s.add(row)
                        s.flush()
                        rows.append(row)
                        prev[row.id] = (None, [], None, None)
                    claimed.add(row.id)
                    touched.add(row.id)
                    old_bbox = list(row.bbox or [])
                    row.worker_item_id = wid or row.worker_item_id
                    row.worker_session = session or row.worker_session
                    row.label = label
                    row.prompt = (str(it.get("prompt"))[:100] if it.get("prompt") else row.prompt)
                    row.score = float(it.get("score") or 0)
                    row.bbox = bbox
                    row.zones = [str(z) for z in (it.get("zones") or [])]
                    row.state = wst
                    ls = from_unix(it.get("last_seen"))
                    if ls is not None:
                        row.last_seen = ls
                    fs = from_unix(it.get("first_seen"))
                    if fs is not None and (row.first_seen is None or fs < _utc(row.first_seen)):
                        row.first_seen = fs
                    if it.get("crop_path"):
                        row.crop_path = str(it["crop_path"])
                    if snapshot_path and wst == "present":
                        row.snapshot_path = str(snapshot_path)
                    row.updated_at = now
                    if changes.get(wid) == "moved" or (old_bbox and len(old_bbox) == 4
                                                         and _center_dist(old_bbox, bbox) > MOVED_HISTORY):
                        moved.add(row.id)
                # fehlende Einträge eines normalen Durchlaufs → missing (Schonfrist nach neuer Session)
                if rt.session_msgs > miss_m:
                    for r in rows:
                        if r.id not in touched and r.state in ("present", "stale"):
                            r.state = "missing"
                            r.updated_at = now
                else:
                    # in der Schonfrist bleiben nicht gemeldete Gegenstände unverändert (stale bleibt stale)
                    pass

            self._update_relations(rows, vocab)
            self._write_history(s, rows, prev, moved, now)
            s.commit()
            self._broadcast(camera_id, rows, prev, vocab)

    def _match_geom(self, rows: list[InventoryItem], label: str, bbox: list[float], claimed: set[int],
                    msg_ids: set[str], session: str) -> Optional[InventoryItem]:
        best, best_score = None, 0.0
        for r in rows:
            if r.id in claimed or r.label != label:
                continue
            # Zeile gehört zu einem anderen, im selben Durchlauf gemeldeten Worker-Gegenstand
            if r.worker_session == session and r.worker_item_id in msg_ids:
                continue
            rb = _bbox(r.bbox)
            if rb is None:
                continue
            iou = _iou(rb, bbox)
            cd = _center_dist(rb, bbox)
            if iou >= MATCH_IOU or cd < MATCH_CENTER:
                sc = iou + (1.0 - min(cd, 1.0)) * 0.5 + (0.2 if r.state == "present" else 0.0)
                if sc > best_score:
                    best, best_score = r, sc
        return best

    def _update_relations(self, rows: list[InventoryItem], vocab: dict[str, dict[str, Any]]) -> None:
        surfaces = [(r.id, r.label, b) for r in rows
                    if r.state == "present" and r.label in names.SURFACE_LABELS and (b := _bbox(r.bbox))]
        for r in rows:
            if r.state != "present":
                continue  # letzte bekannte Lage bleibt für „zuletzt gesehen … vermutlich auf …“ erhalten
            if r.label in names.SURFACE_LABELS:
                rel = None
            else:
                b = _bbox(r.bbox)
                rel = compute_relation(b, [x for x in surfaces if x[0] != r.id], vocab) if b else None
            if rel != (r.relation or None):
                r.relation = rel

    def _write_history(self, s: Session, rows: list[InventoryItem], prev: dict, moved: set[int],
                       now: datetime) -> None:
        tnow = time.time()
        missing_cache = [r.id for r in rows if r.id not in self.last_history]
        if missing_cache:
            for iid, ts in s.execute(select(InventoryHistory.item_id, func.max(InventoryHistory.ts))
                                     .where(InventoryHistory.item_id.in_(missing_cache))
                                     .group_by(InventoryHistory.item_id)).all():
                if ts is not None:
                    self.last_history[iid] = (_utc(ts).timestamp(), [])
        for r in rows:
            old_state = prev.get(r.id, (None,))[0]
            last_t, last_b = self.last_history.get(r.id, (0.0, []))
            write = False
            if r.state != old_state:
                write = True
            elif r.state == "present":
                if tnow - last_t >= HISTORY_PRESENT_EVERY_S:
                    write = True
                elif r.id in moved and tnow - last_t >= HISTORY_MOVED_MIN_S:
                    write = True
                elif (last_b and _bbox(r.bbox) and tnow - last_t >= HISTORY_MOVED_MIN_S
                      and _center_dist(last_b, _bbox(r.bbox)) > MOVED_HISTORY):
                    write = True
            if not write:
                continue
            s.add(InventoryHistory(item_id=r.id, camera_id=r.camera_id, label=r.label, ts=now, state=r.state,
                                   bbox=list(r.bbox or []), zones=list(r.zones or []), score=float(r.score or 0),
                                   crop_path=r.crop_path))
            self.last_history[r.id] = (tnow, list(r.bbox or []))

    def _broadcast(self, camera_id: int, rows: list[InventoryItem], prev: dict,
                   vocab: dict[str, dict[str, Any]]) -> None:
        tnow = time.time()
        cam = camera_info(camera_id)
        out: dict[str, list[dict[str, Any]]] = {"appear": [], "update": [], "gone": []}
        for r in rows:
            old_state, old_bbox, old_crop, old_rel = prev.get(r.id, (None, [], None, None))
            rel_text = (r.relation or {}).get("text")
            if r.state == "present" and old_state != "present":
                action = "appear"
            elif old_state == "present" and r.state != "present":
                action = "gone"
            elif r.state == "present":
                last = self.last_ws.get(r.id)
                ref_bbox, ref_crop, ref_rel = (last[1], last[2], last[3]) if last else (old_bbox, old_crop, old_rel)
                b = _bbox(r.bbox)
                changed = (r.crop_path != ref_crop or rel_text != ref_rel
                           or (b is not None and (not ref_bbox or _center_dist(_bbox(ref_bbox) or b, b) > MOVED_WS)))
                if not changed or (last and tnow - last[0] < WS_UPDATE_MIN_S):
                    continue
                action = "update"
            else:
                continue
            out[action].append(item_json(r, vocab=vocab, cam=cam))
            self.last_ws[r.id] = (tnow, list(r.bbox or []), r.crop_path, rel_text)
        for action in ("gone", "appear", "update"):
            if out[action]:
                hub.broadcast({"type": "inventory", "camera_id": camera_id, "action": action, "items": out[action]})

    # ------------------------------------------------------------ Zeitgeber

    def tick(self) -> None:
        """Kameras ohne Nachricht seit > 3 × interval_s: sichtbare Gegenstände → stale."""
        now = time.time()
        due: list[int] = []
        with self.lock:
            for cam_id, rt in self.cams.items():
                if rt.stale or rt.stopped or not rt.last_rx:
                    continue
                if now - rt.last_rx > STALE_FACTOR * max(rt.interval_s, 1.0):
                    rt.stale = True
                    due.append(cam_id)
            for cam_id in due:
                try:
                    self._mark_stale(cam_id)
                except Exception:  # noqa: BLE001
                    log.exception("Inventar: stale-Markierung für Kamera %s fehlgeschlagen", cam_id)

    def _mark_stale(self, camera_id: int) -> None:
        vocab = names.vocab_map(settings_store.get().get("inventory_vocabulary") or [])
        now = datetime.now(timezone.utc)
        with SessionLocal() as s:
            rows = list(s.scalars(select(InventoryItem).where(InventoryItem.camera_id == camera_id,
                                                               InventoryItem.state == "present")).all())
            if not rows:
                return
            prev = {r.id: ("present", list(r.bbox or []), r.crop_path, (r.relation or {}).get("text")) for r in rows}
            for r in rows:
                r.state = "stale"
                r.updated_at = now
            self._write_history(s, rows, prev, set(), now)
            s.commit()
            log.info("Inventar Kamera %s: keine Daten vom Worker – %d Gegenstände als veraltet markiert",
                     camera_id, len(rows))
            self._broadcast(camera_id, rows, prev, vocab)

    # ------------------------------------------------------------ Verwaltung

    def delete_item(self, s: Session, item: InventoryItem) -> dict[str, Any]:
        """Fehlerkennung entfernen (inkl. Historie); derselbe Worker-Gegenstand wird nicht neu angelegt."""
        with self.lock:
            data = item_json(item)
            if item.worker_session and item.worker_item_id:
                self.suppressed.add((item.camera_id, item.worker_session, item.worker_item_id))
            s.execute(delete(InventoryHistory).where(InventoryHistory.item_id == item.id))
            s.delete(item)
            s.commit()
            self.last_history.pop(item.id, None)
            self.last_ws.pop(item.id, None)
        hub.broadcast({"type": "inventory", "camera_id": data["camera_id"], "action": "delete", "items": [data]})
        return data

    def drop_camera(self, camera_id: int) -> None:
        """Kamera gelöscht: ihre Gegenstände und Historie entfernen."""
        with self.lock, SessionLocal() as s:
            ids = list(s.scalars(select(InventoryItem.id).where(InventoryItem.camera_id == camera_id)).all())
            if ids:
                s.execute(delete(InventoryHistory).where(InventoryHistory.item_id.in_(ids)))
                s.execute(delete(InventoryItem).where(InventoryItem.id.in_(ids)))
                s.commit()
            self.cams.pop(camera_id, None)
            for i in ids:
                self.last_history.pop(i, None)
                self.last_ws.pop(i, None)
            self.suppressed = {k for k in self.suppressed if k[0] != camera_id}


memory = InventoryMemory()
