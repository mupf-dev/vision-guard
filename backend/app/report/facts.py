"""B3 Tagesbericht – Fakten sammeln (rein deterministisch aus der DB, keine KI, keine Bildanalyse).

`collect_facts(db, start, end)` liefert ein JSON-fähiges dict (gespeichert in `reports.facts`, Form siehe
CONTRACT.md W2.G „Ergänzungen B3“). Zeitpunkte sind ISO-8601 UTC; die Texte rechnet `render.py` in Ortszeit um.
Jeder Abschnitt ist auf die wichtigsten Einträge begrenzt (`TOP_N`), damit Bericht und LLM-Eingabe kurz bleiben.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import registry, state
from ..config import config
from ..models import Event, Identity, Notification, PetVisit
from ..schemas import iso

log = logging.getLogger("vg.report")

TOP_N = 8
FACTS_VERSION = 1

# Ereignis-Labels (COCO) → (Einzahl, Mehrzahl, Gruppe)
LABEL_DE: dict[str, tuple[str, str, str]] = {
    "person": ("Person", "Personen", "person"),
    "car": ("Auto", "Autos", "vehicle"),
    "truck": ("Lkw", "Lkw", "vehicle"),
    "bus": ("Bus", "Busse", "vehicle"),
    "motorcycle": ("Motorrad", "Motorräder", "vehicle"),
    "bicycle": ("Fahrrad", "Fahrräder", "vehicle"),
    "dog": ("Hund", "Hunde", "animal"),
    "cat": ("Katze", "Katzen", "animal"),
    "bird": ("Vogel", "Vögel", "animal"),
    "horse": ("Pferd", "Pferde", "animal"),
}
PURPOSE_DE = {"food": "Futternapf", "water": "Wassernapf", "litter": "Katzenklo"}
SOURCE_DE = {"health": "Kamera-Zustand", "pet": "Tiere", "review": "Rückfragen", "sensor": "Sensoren",
             "rule": "Regeln", "report": "Berichte"}
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def label_name(label: str, n: int = 1) -> str:
    one, many, _ = LABEL_DE.get(label, (label, label, "other"))
    return one if n == 1 else many


def label_group(label: str) -> str:
    return LABEL_DE.get(label, ("", "", "other"))[2]


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _identity_name(i: Identity) -> str:
    try:
        return i.display_name
    except Exception:  # noqa: BLE001
        return i.name or f"#{i.id}"


def _events(db: Session, start: datetime, end: datetime) -> dict[str, Any]:
    rng = (Event.start_ts > start, Event.start_ts <= end)
    rows = db.execute(
        select(Event.label, Event.camera_id, Event.camera_name, func.count(), func.min(Event.start_ts),
               func.max(Event.start_ts))
        .where(*rng).group_by(Event.label, Event.camera_id, Event.camera_name)).all()
    by_label: dict[str, dict[str, Any]] = {}
    for label, cid, cname, n, first, last in rows:
        e = by_label.setdefault(label, {"label": label, "name_de": label_name(label, 2), "group": label_group(label),
                                        "count": 0, "first": None, "last": None, "cameras": []})
        e["count"] += int(n)
        f, l_ = _utc(first), _utc(last)
        if f and (e["first"] is None or f < e["first"]):
            e["first"] = f
        if l_ and (e["last"] is None or l_ > e["last"]):
            e["last"] = l_
        e["cameras"].append({"camera_id": cid, "camera_name": cname or f"Kamera {cid}", "count": int(n)})
    out = []
    for e in sorted(by_label.values(), key=lambda x: -x["count"]):
        e["cameras"].sort(key=lambda c: -c["count"])
        e["cameras"] = e["cameras"][:TOP_N]
        e["first"], e["last"] = iso(e["first"]), iso(e["last"])
        out.append(e)
    return {"by_label": out, "total": sum(e["count"] for e in out)}


def _identities(db: Session, start: datetime, end: datetime) -> dict[str, list[dict[str, Any]]]:
    """Identitäten mit Ereignissen im Zeitraum, gruppiert nach Art (face/plate/pet/…)."""
    rng = (Event.start_ts > start, Event.start_ts <= end, Event.identity_id.is_not(None))
    agg = db.execute(
        select(Event.identity_id, func.count(), func.min(Event.start_ts), func.max(Event.start_ts),
               func.min(Event.label))
        .where(*rng).group_by(Event.identity_id)).all()
    if not agg:
        return {}
    ids = [r[0] for r in agg]
    idents = {i.id: i for i in db.scalars(select(Identity).where(Identity.id.in_(ids))).all()}
    cams: dict[int, list[str]] = defaultdict(list)
    for iid, cname, n in db.execute(
            select(Event.identity_id, Event.camera_name, func.count()).where(*rng)
            .group_by(Event.identity_id, Event.camera_name).order_by(func.count().desc())).all():
        if cname and cname not in cams[iid]:
            cams[iid].append(cname)
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for iid, n, first, last, label in agg:
        i = idents.get(iid)
        if i is None:
            continue
        out[i.kind].append({
            "identity_id": iid, "kind": i.kind, "name": i.name, "display_name": _identity_name(i),
            "plate_text": i.plate_text, "label": label, "count": int(n), "first": iso(_utc(first)),
            "last": iso(_utc(last)), "cameras": cams.get(iid, [])[:3],
            "new": bool(i.first_seen and start < _utc(i.first_seen) <= end)})
    for k in out:
        # benannte zuerst, dann nach Häufigkeit
        out[k].sort(key=lambda x: (x["name"] is None, -x["count"], x["first"] or ""))
    return out


def _people(db: Session, start: datetime, end: datetime, ids: dict[str, list[dict]]) -> dict[str, Any]:
    faces = ids.get("face", [])
    named = [f for f in faces if f["name"]]
    unknown = [f for f in faces if not f["name"]]
    no_face = db.scalar(select(func.count()).select_from(Event).where(
        Event.start_ts > start, Event.start_ts <= end, Event.label == "person", Event.identity_id.is_(None))) or 0
    total = db.scalar(select(func.count()).select_from(Event).where(
        Event.start_ts > start, Event.start_ts <= end, Event.label == "person")) or 0
    return {"events": int(total), "named": named[:TOP_N], "unknown_faces": len(unknown),
            "new_faces": sum(1 for f in unknown if f["new"]), "without_face": int(no_face),
            "unknown": unknown[:3]}


def _vehicles(events: dict[str, Any], ids: dict[str, list[dict]]) -> dict[str, Any]:
    by = [e for e in events["by_label"] if e["group"] == "vehicle"]
    plates = ids.get("plate", [])
    return {"total": sum(e["count"] for e in by), "by_label": by, "plates": plates[:TOP_N],
            "plates_total": len(plates)}


def _animals(db: Session, start: datetime, end: datetime, events: dict[str, Any],
             ids: dict[str, list[dict]]) -> dict[str, Any]:
    by = [e for e in events["by_label"] if e["group"] == "animal"]
    pets = ids.get("pet", [])
    visits: list[dict[str, Any]] = []
    try:
        rows = db.execute(
            select(PetVisit.identity_id, PetVisit.label, PetVisit.purpose, func.count(), func.max(PetVisit.start_ts),
                   func.coalesce(func.sum(PetVisit.duration_s), 0))
            .where(PetVisit.start_ts > start, PetVisit.start_ts <= end)
            .group_by(PetVisit.identity_id, PetVisit.label, PetVisit.purpose)).all()
        pid = [r[0] for r in rows if r[0] is not None]
        names = {i.id: _identity_name(i) for i in db.scalars(select(Identity).where(Identity.id.in_(pid))).all()} \
            if pid else {}
        for iid, label, purpose, n, last, dur in rows:
            visits.append({"identity_id": iid, "pet": names.get(iid) if iid else None, "label": label,
                           "purpose": purpose, "purpose_de": PURPOSE_DE.get(purpose, purpose), "count": int(n),
                           "last": iso(_utc(last)), "total_s": round(float(dur or 0))})
        visits.sort(key=lambda v: (v["pet"] is None, v["pet"] or "", v["purpose"]))
    except Exception as e:  # noqa: BLE001 – Tabelle evtl. leer/neu (C3), Bericht darf nicht scheitern
        db.rollback()
        log.debug("Tierbesuche nicht lesbar: %s", e)
    return {"total": sum(e["count"] for e in by), "by_label": by, "named": [p for p in pets if p["name"]][:TOP_N],
            "unnamed": sum(1 for p in pets if not p["name"]), "visits": visits[:TOP_N * 2]}


def _rules(db: Session, start: datetime, end: datetime) -> list[dict[str, Any]]:
    hits: dict[str, dict[str, Any]] = {}
    rows = db.execute(select(Event.rules, Event.start_ts, Event.snapshot_path).where(
        Event.start_ts > start, Event.start_ts <= end, func.jsonb_array_length(Event.rules) > 0)).all()
    for names, ts, snap in rows:
        for name in names or []:
            h = hits.setdefault(str(name), {"name": str(name), "count": 0, "first": None, "last": None,
                                            "snapshot_path": None})
            _hit(h, _utc(ts), snap)
    # Regeln ohne Event (z. B. trigger=time) stehen nur als Notification
    for name, ts, img in db.execute(select(Notification.rule_name, Notification.ts, Notification.image_path).where(
            Notification.source == "rule", Notification.event_id.is_(None), Notification.ts > start,
            Notification.ts <= end)).all():
        if not name:
            continue
        h = hits.setdefault(name, {"name": name, "count": 0, "first": None, "last": None, "snapshot_path": None})
        _hit(h, _utc(ts), img)
    out = sorted(hits.values(), key=lambda h: (-h["count"], h["name"]))[:TOP_N]
    for h in out:
        h["first"], h["last"] = iso(h["first"]), iso(h["last"])
        p = h.pop("snapshot_path")
        h["snapshot_url"] = f"/media/{p}" if p else None
    return out


def _hit(h: dict[str, Any], ts: Optional[datetime], img: Optional[str]) -> None:
    h["count"] += 1
    if ts and (h["first"] is None or ts < h["first"]):
        h["first"] = ts
    if ts and (h["last"] is None or ts >= h["last"]):
        h["last"] = ts
        if img:
            h["snapshot_path"] = img


def _notifications(db: Session, start: datetime, end: datetime) -> dict[str, Any]:
    by = dict(db.execute(select(Notification.source, func.count()).where(
        Notification.ts > start, Notification.ts <= end, Notification.source.not_in(("rule", "report")))
        .group_by(Notification.source)).all())
    items = db.execute(select(Notification.ts, Notification.source, Notification.level, Notification.title).where(
        Notification.ts > start, Notification.ts <= end, Notification.source.not_in(("rule", "report")))
        .order_by(Notification.ts.desc()).limit(TOP_N)).all()
    return {"by_source": {k: int(v) for k, v in by.items()},
            "items": [{"ts": iso(_utc(ts)), "source": src, "source_de": SOURCE_DE.get(src, src), "level": lvl,
                       "title": title} for ts, src, lvl, title in items]}


def _inventory(db: Session, start: datetime, end: datetime) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    from ..inventory_diff import ChangesError, compute_changes   # Import hier: inventory zieht viel nach

    try:
        # now=end: Texte („14:02“, „gestern …“) beziehen sich auf das Berichtsende, nicht auf den Abrufzeitpunkt
        ch = compute_changes(db, start, end, limit=TOP_N * 2, now=end)
    except ChangesError as e:
        return None, str(e)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("Gegenstands-Veränderungen für den Bericht fehlgeschlagen: %s", e)
        return None, "Gegenstände nicht auswertbar"
    changes = []
    for c in ch.get("changes") or []:
        it = c.get("item") or {}
        changes.append({
            "type": c.get("type"), "at": c.get("at"), "text": c.get("text"),
            "item_id": it.get("id"), "label": it.get("label"), "name_de": it.get("name_de"),
            "camera_id": it.get("camera_id"), "camera_name": it.get("camera_name"), "room": it.get("room"),
            "before": _snap(c.get("before")), "after": _snap(c.get("after"))})
    summary = ch.get("summary") or ""
    # „Seit 21:00: …“ → nur der Teil hinter dem Doppelpunkt (die Zeitspanne steht im Bericht schon)
    if ": " in summary:
        summary = summary.split(": ", 1)[1]
    return {"summary": summary, "counts": ch.get("counts") or {}, "changes": changes}, None


def _snap(s: Optional[dict]) -> Optional[dict[str, Any]]:
    if not s:
        return None
    return {"ts": s.get("ts"), "state": s.get("state"), "zone_names": s.get("zone_names") or [],
            "crop_url": s.get("crop_url")}


def _system() -> dict[str, Any]:
    cams = registry.all()
    offline = []
    for c in cams:
        if not c.get("enabled", True):
            continue
        st = state.status_for(c["id"], True)
        if st.get("state") == "offline":
            offline.append({"id": c["id"], "name": c.get("name") or f"Kamera {c['id']}"})
    return {"cameras": [{"id": c["id"], "name": c.get("name"), "room": c.get("room")} for c in cams],
            "offline_cameras": offline}


def collect_facts(db: Session, start: datetime, end: datetime, *, with_system: bool = True) -> dict[str, Any]:
    """Alle Fakten für den Zeitraum (start, end]."""
    start, end = _utc(start), _utc(end)
    loc_end = end.astimezone(config.tz)
    if loc_end.hour == 0 and loc_end.minute == 0 and (end - start).total_seconds() >= 3600:
        loc_end = (end - timedelta(seconds=1)).astimezone(config.tz)   # Kalendertag: Bericht gehört zum Vortag
    events = _events(db, start, end)
    ids = _identities(db, start, end)
    inv, inv_err = _inventory(db, start, end)
    facts: dict[str, Any] = {
        "version": FACTS_VERSION,
        "period": {"start": iso(start), "end": iso(end), "date": loc_end.date().isoformat(),
                   "weekday": WEEKDAYS[loc_end.weekday()], "hours": round((end - start).total_seconds() / 3600, 1)},
        "events": events["by_label"],
        "people": _people(db, start, end, ids),
        "vehicles": _vehicles(events, ids),
        "animals": _animals(db, start, end, events, ids),
        "rules": _rules(db, start, end),
        "inventory": inv,
        "notifications": _notifications(db, start, end),
        "system": _system() if with_system else {"cameras": [], "offline_cameras": []},
    }
    if inv_err:
        facts["inventory_error"] = inv_err
    facts["counts"] = counts(facts, events["total"])
    return facts


def counts(facts: dict[str, Any], events_total: Optional[int] = None) -> dict[str, int]:
    inv = facts.get("inventory") or {}
    return {
        "events": int(events_total if events_total is not None else sum(e["count"] for e in facts.get("events", []))),
        "person": int((facts.get("people") or {}).get("events") or 0),
        "named_people": len((facts.get("people") or {}).get("named") or []),
        "vehicle": int((facts.get("vehicles") or {}).get("total") or 0),
        "animal": int((facts.get("animals") or {}).get("total") or 0),
        "rules": sum(int(r.get("count") or 0) for r in facts.get("rules") or []),
        "inventory": sum(int(v or 0) for k, v in (inv.get("counts") or {}).items() if k != "transient"),
        "notifications": sum(int(v or 0) for v in ((facts.get("notifications") or {}).get("by_source") or {}).values()),
    }
