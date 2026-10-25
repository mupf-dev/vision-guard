"""B2 „Was hat sich verändert?“ – Vergleich des Gegenstandsgedächtnisses zwischen zwei Zeitpunkten.

Grundlage ist `inventory_history` (Eintrag bei jedem Zustandswechsel, sonst höchstens alle 5 min bzw. bei
Verschiebung 1×/min). Ergebnis: neu aufgetaucht / verschwunden / verschoben / Stand unklar / kurz sichtbar
(CONTRACT.md, Nachträge Welle 1, W1.E).

Ablauf je Gegenstand:
1. Aus der Historie eine Zeitleiste aus Abschnitten `present` / `absent` (missing oder noch nicht vorhanden) /
   `stale` bilden.
2. **Rauschfilter (Entprellung):** Abschnitte kürzer als `inventory_change_min_s` werden in die Nachbarn
   eingeschmolzen – kurzes Flackern, kurze Worker-Neustarts (`stale` für 30–60 s) und Fehltreffer zählen nicht.
   Ein noch laufender Abschnitt am Ende (bis `until`) zählt erst, wenn er die Mindestdauer erreicht hat.
3. Zustand zu `since` mit Zustand zu `until` vergleichen.
4. **Paarung:** `disappeared` + `appeared` mit gleichem Label an derselben Kamera (Worker hat den Gegenstand als
   neue Zeile angelegt) → ein `moved`; liegt der neue Fund an derselben Stelle (Mittelpunkt ≤ 0,10, gleiche
   Zonen), ist es derselbe Gegenstand und es wird gar nichts gemeldet.

Der Worker bleibt unverändert; es gibt keine Bildanalyse.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import inventory_names as names
from . import settings_store
from .config import config
from .inventory import _bbox, _center_dist, _media, camera_info, compute_relation, item_json, local_time_text
from .models import Camera, InventoryHistory, InventoryItem
from .schemas import iso

MOVED_MIN = 0.10           # Mittelpunktverschiebung (normiert) für „verschoben“
TYPE_ORDER = ("disappeared", "appeared", "moved", "uncertain", "transient")
SUMMARY_NAMES = 3          # so viele Namen je Kategorie im Satz `summary`

PRESENT, ABSENT, STALE = "present", "absent", "stale"
NEG_INF = datetime.min.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- Zeitangaben


class WhenError(ValueError):
    """Ungültige Zeitangabe (deutsche Meldung für 422)."""


_REL = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(s|sek|m|min|h|std|d|t|tag|tage|w|wo)$")
_REL_UNIT = {"s": 1, "sek": 1, "m": 60, "min": 60, "h": 3600, "std": 3600, "d": 86400, "t": 86400,
             "tag": 86400, "tage": 86400, "w": 604800, "wo": 604800}
_KEYWORDS = {
    "now": "now", "jetzt": "now",
    "today": "today", "heute": "today",
    "morning": "morning", "heute morgen": "morning", "heute früh": "morning", "seit heute morgen": "morning",
    "yesterday": "yesterday", "gestern": "yesterday",
}
MORNING = dtime(6, 0)


def parse_when(value: Optional[str], now: Optional[datetime] = None) -> datetime:
    """Zeitangabe → UTC-Zeitpunkt.

    Erlaubt: ISO-8601 (ohne Zeitzone = lokal), relativ `3h`, `90m`, `2d`, `1w`, `45s` (auch `90 min`, `2 Tage`),
    `today`/`heute` (lokal 00:00), `morning`/`heute morgen` (lokal 06:00; vor 06:00 der gestrige Morgen),
    `yesterday`/`gestern` (gestern 00:00), `now`/`jetzt`."""
    now = now or datetime.now(timezone.utc)
    raw = (value or "").strip()
    if not raw:
        raise WhenError("Zeitangabe fehlt")
    v = raw.lower()
    tz = config.tz
    kw = _KEYWORDS.get(v)
    if kw:
        loc = now.astimezone(tz)
        if kw == "now":
            return now
        if kw == "today":
            return datetime.combine(loc.date(), dtime(0, 0), tz).astimezone(timezone.utc)
        if kw == "yesterday":
            return datetime.combine(loc.date() - timedelta(days=1), dtime(0, 0), tz).astimezone(timezone.utc)
        m = datetime.combine(loc.date(), MORNING, tz)
        if m > loc:
            m = datetime.combine(loc.date() - timedelta(days=1), MORNING, tz)
        return m.astimezone(timezone.utc)
    m = _REL.match(v)
    if m:
        amount = float(m.group(1).replace(",", "."))
        return now - timedelta(seconds=amount * _REL_UNIT[m.group(2)])
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise WhenError(f"Unbekannte Zeitangabe „{raw}“ (erlaubt: ISO-8601, 3h, 90m, 2d, today, morning, "
                        "yesterday)") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------- Zeitleiste (rein, ohne DB – testbar)


def _cls(state: Optional[str]) -> str:
    if state == "present":
        return PRESENT
    if state == "stale":
        return STALE
    return ABSENT


@dataclass
class Segment:
    start: datetime
    end: datetime
    cls: str

    @property
    def duration(self) -> float:
        if self.start == NEG_INF:
            return math.inf
        return (self.end - self.start).total_seconds()


def build_segments(points: Iterable[tuple[datetime, str]], until: datetime,
                   initial: Optional[str] = None) -> list[Segment]:
    """Historienpunkte (ts, state) → zusammenhängende Abschnitte bis `until`. `initial` = Zustand vor dem ersten
    Punkt (Ausgangszustand vor dem Zeitfenster, gilt als beliebig lang); None = Gegenstand existierte noch nicht."""
    segs: list[Segment] = [Segment(NEG_INF, until, _cls(initial) if initial else ABSENT)]
    for ts, state in sorted(points, key=lambda p: p[0]):
        if ts > until:
            break
        c = _cls(state)
        if c == segs[-1].cls:
            continue
        if ts <= segs[-1].start:      # gleicher Zeitpunkt: letzter Eintrag gewinnt
            segs[-1].cls = c
            if len(segs) > 1 and segs[-2].cls == c:
                segs.pop()
                segs[-1].end = until
            continue
        segs[-1].end = ts
        segs.append(Segment(ts, until, c))
    segs[-1].end = until
    return segs


def debounce(segs: list[Segment], min_s: float) -> list[Segment]:
    """Abschnitte kürzer als `min_s` einschmelzen (kürzester zuerst). Gleiche Nachbarn verschmelzen; sonst geht
    der kurze Abschnitt im vorherigen auf (der Zustand galt weiter, bis der nächste stabile begann)."""
    segs = [Segment(s.start, s.end, s.cls) for s in segs]
    if min_s <= 0:
        return segs
    while len(segs) > 1:
        short = [(s.duration, i) for i, s in enumerate(segs) if i > 0 and s.duration < min_s]
        if not short:
            break
        _, i = min(short)
        prev = segs[i - 1]
        nxt = segs[i + 1] if i + 1 < len(segs) else None
        if nxt is not None and nxt.cls == prev.cls:
            prev.end = nxt.end
            del segs[i:i + 2]
        else:
            prev.end = segs[i].end
            del segs[i]
    return segs


def seg_at(segs: list[Segment], t: datetime) -> Segment:
    for s in segs:
        if s.start <= t < s.end:
            return s
    return segs[-1]


def known_cls_at(segs: list[Segment], t: datetime) -> str:
    """Zustand zu `t`; `stale` wird durch den letzten bekannten Zustand davor ersetzt."""
    s = seg_at(segs, t)
    if s.cls != STALE:
        return s.cls
    idx = segs.index(s)
    for p in reversed(segs[:idx]):
        if p.cls != STALE:
            return p.cls
    return ABSENT


@dataclass
class Verdict:
    type: Optional[str]          # appeared|disappeared|moved?|uncertain|transient|None
    at: Optional[datetime] = None
    check_moved: bool = False
    after_cls: str = ABSENT


def classify(points: list[tuple[datetime, str]], since: datetime, until: datetime, min_s: float,
             initial: Optional[str] = None, stale_as_absent: bool = False) -> Verdict:
    """Zustand zu `since` gegen Zustand zu `until` (beides entprellt). `stale_as_absent`: die Kamera liefert zu
    `until` nachweislich wieder Daten (andere Gegenstände sichtbar) – ein `stale` am Ende ist dann eine
    Altlast (z. B. nach Worker-Neustart als neue Zeile erkannt) und zählt als nicht mehr da."""
    raw = build_segments(points, until, initial)
    segs = debounce(raw, min_s)
    after = segs[-1]                      # alle Abschnitte enden spätestens bei `until`
    before_cls = known_cls_at(segs, since)
    a_cls = after.cls
    if a_cls == STALE:
        if not stale_as_absent:
            return Verdict("uncertain", after.start if after.start != NEG_INF else since, after_cls=STALE)
        a_cls = ABSENT
    if before_cls == ABSENT and a_cls == PRESENT:
        return Verdict("appeared", after.start, after_cls=PRESENT)
    if before_cls == PRESENT and a_cls == ABSENT:
        return Verdict("disappeared", after.start)
    if before_cls == PRESENT and a_cls == PRESENT:
        return Verdict(None, None, check_moved=True, after_cls=PRESENT)
    # vorher und nachher nicht da: im Zeitraum kurz sichtbar?
    # nur abgeschlossene Abschnitte – ein noch laufender, junger Fund ist (noch) unbestätigt
    shown = [s for s in raw[:-1] if s.cls == PRESENT and s.end > since]
    if shown:
        return Verdict("transient", max(shown[-1].start, since))
    return Verdict(None)


# ---------------------------------------------------------------- Texte


def _dative(label: str, name: str) -> str:
    return names._DATIVE.get(label) or f"„{name}“"


def _from_to(rel: Optional[dict], prefix: str) -> Optional[str]:
    """„vom Tisch“ / „zum Sofa“ für `on`-Beziehungen."""
    if not rel or rel.get("type") != "on":
        return None
    d = _dative(rel.get("label") or "", rel.get("name_de") or "")
    if d.startswith("dem "):
        return ("vom " if prefix == "von" else "zum ") + d[4:]
    return f"{prefix} {d}"


def _plain_rel(rel: Optional[dict]) -> Optional[str]:
    t = (rel or {}).get("text")
    return t.replace("vermutlich ", "", 1) if t else None


def _place(cam: dict[str, Any], zone_names: list[str]) -> str:
    base = cam.get("room") or cam.get("name") or "?"
    if cam.get("room") and cam.get("name") and cam["name"] != cam["room"]:
        base = f"{cam['room']}, Kamera {cam['name']}"
    if zone_names:
        base += ", " + ("Zone " if len(zone_names) == 1 else "Zonen ") + ", ".join(zone_names)
    return base


def _short_place(cam: dict[str, Any]) -> str:
    return cam.get("room") or cam.get("name") or "?"


# ---------------------------------------------------------------- DB-Zugriff


@dataclass
class _Row:
    ts: datetime
    state: str
    camera_id: int
    bbox: list[float]
    zones: list[str]
    crop_path: Optional[str]


def _row(h: InventoryHistory) -> _Row:
    return _Row(_utc(h.ts), h.state, h.camera_id, list(h.bbox or []), [str(z) for z in (h.zones or [])],
                h.crop_path)


def _last_rows(db: Session, item_ids: list[int], t: datetime, present_only: bool = False) -> dict[int, _Row]:
    """Je Gegenstand der letzte Historien-Eintrag mit ts ≤ t (DISTINCT ON)."""
    if not item_ids:
        return {}
    q = (select(InventoryHistory).where(InventoryHistory.item_id.in_(item_ids), InventoryHistory.ts <= t)
         .order_by(InventoryHistory.item_id, InventoryHistory.ts.desc(), InventoryHistory.id.desc())
         .distinct(InventoryHistory.item_id))
    if present_only:
        q = q.where(InventoryHistory.state == "present")
    return {h.item_id: _row(h) for h in db.scalars(q).all()}


def state_at(db: Session, t: datetime, item_ids: list[int]) -> dict[int, _Row]:
    """Stand aller Gegenstände zu `t` (letzter Eintrag ≤ t). Gegenstände ohne Eintrag existierten noch nicht."""
    return _last_rows(db, item_ids, t)


def _transitions(db: Session, item_ids: list[int], t0: datetime,
                 until: datetime) -> tuple[dict[int, Optional[str]], dict[int, list[tuple[datetime, str]]]]:
    """Ausgangszustand (letzter Eintrag ≤ t0) und alle Zustandswechsel in (t0, until] je Gegenstand.
    Die Wechsel ermittelt die Datenbank per LAG (periodische `present`-Einträge werden nicht übertragen)."""
    out: dict[int, list[tuple[datetime, str]]] = {i: [] for i in item_ids}
    if not item_ids:
        return {}, out
    initial = {iid: r.state for iid, r in _last_rows(db, item_ids, t0).items()}
    prev = func.lag(InventoryHistory.state).over(partition_by=InventoryHistory.item_id,
                                                 order_by=(InventoryHistory.ts, InventoryHistory.id))
    sub = (select(InventoryHistory.item_id.label("item_id"), InventoryHistory.ts.label("ts"),
                  InventoryHistory.state.label("state"), prev.label("prev"))
           .where(InventoryHistory.item_id.in_(item_ids), InventoryHistory.ts > t0, InventoryHistory.ts <= until)
           .subquery())
    q = select(sub.c.item_id, sub.c.ts, sub.c.state).where((sub.c.prev.is_(None)) | (sub.c.prev != sub.c.state))
    for iid, ts, st in db.execute(q).all():
        out[iid].append((_utc(ts), st))
    return initial, out


def _present_rows(db: Session, item_id: int, since: datetime, until: datetime) -> list[_Row]:
    q = (select(InventoryHistory).where(InventoryHistory.item_id == item_id, InventoryHistory.state == "present",
                                        InventoryHistory.ts > since, InventoryHistory.ts <= until)
         .order_by(InventoryHistory.ts, InventoryHistory.id))
    return [_row(h) for h in db.scalars(q).all()]


# ---------------------------------------------------------------- Hauptfunktion


@dataclass
class _Change:
    type: str
    at: datetime
    item: InventoryItem
    before: Optional[_Row] = None
    after: Optional[_Row] = None
    rel_before: Optional[dict] = None
    rel_after: Optional[dict] = None
    extra: dict = field(default_factory=dict)


class ChangesError(ValueError):
    pass


def _camera_ids_for_room(db: Session, room: str) -> list[int]:
    return list(db.scalars(select(Camera.id).where(func.lower(Camera.room) == room.strip().lower())).all())


def _moved(b: Optional[_Row], a: Optional[_Row]) -> bool:
    if not b or not a:
        return False
    bb, ab = _bbox(b.bbox), _bbox(a.bbox)
    if bb is None or ab is None:
        return False
    return _center_dist(bb, ab) > MOVED_MIN or set(b.zones) != set(a.zones)


def compute_changes(db: Session, since: datetime, until: Optional[datetime] = None, *,
                    camera_id: Optional[int] = None, room: Optional[str] = None,
                    labels: Optional[list[str]] = None, include_transient: bool = False,
                    limit: int = 100, min_s: Optional[float] = None,
                    now: Optional[datetime] = None) -> dict[str, Any]:
    """InventoryChanges (CONTRACT W1.E) für den Zeitraum (since, until]."""
    st = settings_store.get()
    now = now or datetime.now(timezone.utc)
    until = min(until or now, now)
    since = _utc(since)
    if since >= until:
        raise ChangesError("„since“ muss vor „until“ bzw. vor jetzt liegen")
    keep_days = int(st.get("retention_days_inventory") or 30)
    if since < now - timedelta(days=keep_days):
        raise ChangesError("Zeitraum außerhalb der Aufbewahrung")
    if min_s is None:
        min_s = float(st.get("inventory_change_min_s") if st.get("inventory_change_min_s") is not None else 120)
    vocab = names.vocab_map(st.get("inventory_vocabulary") or [])

    conds = []
    if camera_id is not None:
        conds.append(InventoryItem.camera_id == camera_id)
    if room:
        conds.append(InventoryItem.camera_id.in_(_camera_ids_for_room(db, room) or [-1]))
    if labels:
        conds.append(InventoryItem.label.in_(labels))
    # Gegenstände, die erst nach `until` auftauchten, spielen keine Rolle
    conds.append(InventoryItem.first_seen <= until)
    items = {r.id: r for r in db.scalars(select(InventoryItem).where(*conds)).all()}
    ids = list(items)

    # Ausgangszustand etwas vor `since`, damit die Entprellung auch Abschnitte um `since` beurteilen kann
    t0 = since - timedelta(seconds=min_s + 600)
    initial, trans = _transitions(db, ids, t0, until)
    verdicts: dict[int, Verdict] = {iid: classify(trans[iid], since, until, min_s, initial.get(iid))
                                    for iid in ids}
    # Kameras, die zu `until` nachweislich Daten lieferten: dortige `stale`-Zeilen sind Altlasten
    alive = {items[i].camera_id for i, v in verdicts.items() if v.after_cls == PRESENT}
    for iid, v in list(verdicts.items()):
        if v.type == "uncertain" and items[iid].camera_id in alive:
            verdicts[iid] = classify(trans[iid], since, until, min_s, initial.get(iid), stale_as_absent=True)
    involved = [i for i, v in verdicts.items() if v.type or v.check_moved]
    last_present_until = _last_rows(db, involved, until, present_only=True)
    last_until = _last_rows(db, involved, until)
    moved_cands = [i for i in involved if verdicts[i].check_moved]
    last_present_since = _last_rows(db, moved_cands, since, present_only=True)

    changes: list[_Change] = []
    appeared: list[_Change] = []
    disappeared: list[_Change] = []
    for iid in involved:
        v, it = verdicts[iid], items[iid]
        if v.check_moved:
            b, a = last_present_since.get(iid), last_present_until.get(iid)
            if not _moved(b, a):
                continue
            # Zeitpunkt: frühester Eintrag, ab dem der Gegenstand (bis `until`) am neuen Ort lag
            at = a.ts
            ab = _bbox(a.bbox)
            for r in reversed(_present_rows(db, iid, since, until)):
                rb = _bbox(r.bbox)
                if rb is None or ab is None or _center_dist(rb, ab) > MOVED_MIN or set(r.zones) != set(a.zones):
                    break
                at = r.ts
            changes.append(_Change("moved", at, it, b, a))
        elif v.type == "appeared":
            c = _Change("appeared", v.at, it, None, last_present_until.get(iid))
            appeared.append(c)
        elif v.type == "disappeared":
            gone = last_until.get(iid)
            c = _Change("disappeared", v.at, it, last_present_until.get(iid), gone)
            disappeared.append(c)
        elif v.type == "uncertain":
            changes.append(_Change("uncertain", v.at, it, last_present_until.get(iid), last_until.get(iid)))
        elif v.type == "transient":
            rows = _present_rows(db, iid, since, until) if include_transient else []
            last = rows[-1] if rows else last_present_until.get(iid)
            c = _Change("transient", v.at, it, None, last)
            if rows:
                c.extra["first"] = rows[0].ts
            c.extra["last"] = last.ts if last else None
            changes.append(c)

    # Paarung: verschwunden + neu (gleiches Label, gleiche Kamera) → verschoben bzw. derselbe Gegenstand
    pairs = []
    for di, d in enumerate(disappeared):
        for ai, a in enumerate(appeared):
            if d.item.camera_id != a.item.camera_id or d.item.label != a.item.label:
                continue
            db_, ab_ = _bbox(d.before.bbox) if d.before else None, _bbox(a.after.bbox) if a.after else None
            dist = _center_dist(db_, ab_) if db_ and ab_ else 1.5
            pairs.append((dist, di, ai))
    used_d: set[int] = set()
    used_a: set[int] = set()
    for dist, di, ai in sorted(pairs):
        if di in used_d or ai in used_a:
            continue
        used_d.add(di)
        used_a.add(ai)
        d, a = disappeared[di], appeared[ai]
        same_zones = set(d.before.zones if d.before else []) == set(a.after.zones if a.after else [])
        if dist <= MOVED_MIN and same_zones:
            continue  # derselbe Gegenstand an derselben Stelle (z. B. nach Worker-Neustart neu angelegt)
        changes.append(_Change("moved", a.at, a.item, d.before, a.after, extra={"paired": d.item.id}))
    # verschwunden, aber an derselben Stelle steht eine neuere Zeile mit unklarem Stand → derselbe Gegenstand,
    # nur „Stand unklar“ melden
    unclear = [(c.item.camera_id, c.item.label, _bbox(c.before.bbox)) for c in changes
               if c.type == "uncertain" and c.before is not None and _bbox(c.before.bbox)]
    for i, d in enumerate(disappeared):
        b = _bbox(d.before.bbox) if d.before else None
        if i not in used_d and b is not None and any(
                cid == d.item.camera_id and lb == d.item.label and _center_dist(ub, b) <= MOVED_MIN
                for cid, lb, ub in unclear):
            used_d.add(i)
    changes += [d for i, d in enumerate(disappeared) if i not in used_d]
    changes += [a for i, a in enumerate(appeared) if i not in used_a]
    # „kurz sichtbar“, das nur die alte bzw. neue Zeile desselben Gegenstands ist (Worker-Neustart: neue Zeile an
    # derselben Stelle), nicht melden
    anchors: list[tuple[_Change, Optional[_Row]]] = [(c, c.after or c.before) for c in changes if c.type != "transient"]
    anchors += [(x, x.before) for i, x in enumerate(disappeared) if i in used_d]
    anchors += [(x, x.after) for i, x in enumerate(appeared) if i in used_a]

    def _shadow(c: _Change) -> bool:
        b = _bbox(c.after.bbox) if c.after else None
        if b is None:
            return False
        for a, r in anchors:
            ab = _bbox(r.bbox) if r else None
            if (a.item.camera_id == c.item.camera_id and a.item.label == c.item.label and ab is not None
                    and _center_dist(ab, b) <= MOVED_MIN):
                if a.type == "appeared" and c.at < a.at:
                    a.at = c.at    # tatsächlich schon seit dem Fund der alten Zeile da
                return True
        return False

    shadowed = {id(c) for c in changes if c.type == "transient" and _shadow(c)}   # passt dabei `at` an
    changes = [c for c in changes
               if c.type != "transient" or (include_transient and id(c) not in shadowed)]

    # Lagebeziehungen vorher/nachher aus den Flächen (Tisch, Sofa …) zum jeweiligen Zeitpunkt
    _attach_relations(db, changes, since, until, vocab)

    counts = {t: 0 for t in TYPE_ORDER}
    for c in changes:
        counts[c.type] += 1
    changes.sort(key=lambda c: (TYPE_ORDER.index(c.type), -c.at.timestamp()))
    cams: dict[int, dict] = {}

    def cam(cid: int) -> dict:
        return cams.setdefault(cid, camera_info(cid))

    out = [_change_json(c, cam(c.item.camera_id), vocab, now) for c in changes[:limit]]
    summary = _summary(changes, since, until, now, cam, vocab)
    return {"since": iso(since), "until": iso(until), "summary": summary, "counts": counts, "changes": out}


def _attach_relations(db: Session, changes: list[_Change], since: datetime, until: datetime,
                      vocab: dict[str, dict]) -> None:
    cam_ids = {c.item.camera_id for c in changes if c.type in ("moved", "appeared", "disappeared", "transient")}
    if not cam_ids:
        return
    surf_items = list(db.scalars(select(InventoryItem).where(InventoryItem.camera_id.in_(cam_ids),
                                                             InventoryItem.label.in_(names.SURFACE_LABELS))).all())
    sids = [s.id for s in surf_items]
    by_id = {s.id: s for s in surf_items}
    cache: dict[datetime, dict[int, _Row]] = {}

    def surfaces(camera_id: int, t: datetime, exclude: int) -> list[tuple[int, str, list[float]]]:
        if t not in cache:
            cache[t] = _last_rows(db, sids, t)
        out = []
        for sid, r in cache[t].items():
            s = by_id[sid]
            b = _bbox(r.bbox)
            if s.camera_id == camera_id and r.state == "present" and sid != exclude and b is not None:
                out.append((sid, s.label, b))
        return out

    for c in changes:
        if c.item.label in names.SURFACE_LABELS:
            continue
        if c.before is not None and c.before.state == "present" and (b := _bbox(c.before.bbox)):
            c.rel_before = compute_relation(b, surfaces(c.item.camera_id, c.before.ts, c.item.id), vocab)
        if c.after is not None and c.after.state == "present" and (a := _bbox(c.after.bbox)):
            c.rel_after = compute_relation(a, surfaces(c.item.camera_id, c.after.ts, c.item.id), vocab)


def _snap(r: Optional[_Row], cam: dict[str, Any]) -> Optional[dict[str, Any]]:
    if r is None:
        return None
    zmap = cam.get("zones") or {}
    return {"ts": iso(r.ts), "state": r.state, "camera_id": r.camera_id,
            "bbox": [round(float(x), 4) for x in r.bbox], "zones": list(r.zones),
            "zone_names": [zmap.get(z) or z for z in r.zones],
            # Ausschnitt nur, wenn der Gegenstand zu diesem Zeitpunkt sichtbar war
            "crop_url": _media(r.crop_path, False) if r.state == "present" else None}


def _zone_names(r: Optional[_Row], cam: dict[str, Any]) -> list[str]:
    zmap = cam.get("zones") or {}
    return [zmap.get(z) or z for z in (r.zones if r else [])]


def _change_text(c: _Change, cam: dict[str, Any], name: str, now: datetime) -> str:
    zb, za = _zone_names(c.before, cam), _zone_names(c.after, cam)
    if c.type == "appeared":
        t = f"{name}: neu aufgetaucht ({_place(cam, za)})"
        if c.rel_after:
            t += f", {c.rel_after['text']}"
        return t
    if c.type == "disappeared":
        t = f"{name}: verschwunden ({_place(cam, zb)})"
        if c.rel_before:
            t += f", zuvor {c.rel_before['text']}"
        return t
    if c.type == "moved":
        f, to = _from_to(c.rel_before, "von"), _from_to(c.rel_after, "zu")
        rb, ra = (c.rel_before or {}).get("label"), (c.rel_after or {}).get("label")
        if f and to and rb != ra:
            how = f"vermutlich {f} {to} verschoben"
        elif (c.rel_before or c.rel_after) and (c.rel_before or {}).get("text") != (c.rel_after or {}).get("text"):
            how = (f"vermutlich verschoben – vorher {_plain_rel(c.rel_before) or 'anderswo'}, "
                   f"jetzt {_plain_rel(c.rel_after) or 'an anderer Stelle'}")
        elif zb != za and (zb or za):
            how = (f"vermutlich verschoben von {'Zone ' + ', '.join(zb) if zb else 'außerhalb der Zonen'} "
                   f"nach {'Zone ' + ', '.join(za) if za else 'außerhalb der Zonen'}")
        else:
            how = "vermutlich an eine andere Stelle verschoben"
        return f"{name}: {how} ({_short_place(cam)})"
    if c.type == "uncertain":
        return (f"{name}: Stand unklar – Kamera {cam.get('name') or '?'} liefert seit {local_time_text(c.at, now)} "
                f"keine Gegenstandsanalyse")
    if c.type == "transient":
        first, last = c.extra.get("first") or c.at, c.extra.get("last") or c.at
        span = local_time_text(first, now)
        if last and abs((last - first).total_seconds()) >= 60:
            span += f"–{local_time_text(last, now).split(' ')[-1]}"
        return f"{name}: zwischendurch da, inzwischen wieder weg ({span}, {_place(cam, za)})"
    return name


def _change_json(c: _Change, cam: dict[str, Any], vocab: dict[str, dict], now: datetime) -> dict[str, Any]:
    name = names.display_name(c.item.label, vocab)
    return {"type": c.type, "at": iso(c.at), "item": item_json(c.item, vocab=vocab, cam=cam),
            "before": _snap(c.before, cam), "after": _snap(c.after, cam),
            "text": _change_text(c, cam, name, now)}


def _summary(changes: list[_Change], since: datetime, until: datetime, now: datetime, cam, vocab) -> str:
    if (now - until).total_seconds() < 60:
        head = f"Seit {local_time_text(since, now)}"
    else:
        head = f"Zwischen {local_time_text(since, now)} und {local_time_text(until, now)}"
    by: dict[str, list[_Change]] = {t: [] for t in TYPE_ORDER}
    for c in sorted(changes, key=lambda c: -c.at.timestamp()):
        by[c.type].append(c)

    def names_of(lst: list[_Change], fmt) -> str:
        parts = [fmt(c) for c in lst[:SUMMARY_NAMES]]
        if len(lst) > SUMMARY_NAMES:
            parts.append(f"+{len(lst) - SUMMARY_NAMES} weitere")
        return ", ".join(parts)

    def nm(c: _Change) -> str:
        return names.display_name(c.item.label, vocab)

    def with_place(c: _Change) -> str:
        return f"{nm(c)} ({_short_place(cam(c.item.camera_id))})"

    def moved_fmt(c: _Change) -> str:
        rb, ra = c.rel_before, c.rel_after
        if rb and ra and rb.get("label") != ra.get("label"):
            return f"{nm(c)}: {rb.get('name_de')} → {ra.get('name_de')}"
        return with_place(c)

    parts = []
    if by["appeared"]:
        parts.append(f"{len(by['appeared'])} neu ({names_of(by['appeared'], with_place)})")
    if by["disappeared"]:
        parts.append(f"{len(by['disappeared'])} weg ({names_of(by['disappeared'], with_place)})")
    if by["moved"]:
        parts.append(f"{len(by['moved'])} verschoben ({names_of(by['moved'], moved_fmt)})")
    if by["transient"]:
        parts.append(f"{len(by['transient'])} zwischendurch da ({names_of(by['transient'], nm)})")
    text = f"{head}: " + (", ".join(parts) if parts else "keine Veränderungen") + "."
    if by["uncertain"]:
        cams_ = sorted({cam(c.item.camera_id).get("name") or "?" for c in by["uncertain"]})
        n = len(by["uncertain"])
        text += (f" Bei {n} {'Gegenstand' if n == 1 else 'Gegenständen'} ist der Stand unklar (Kamera "
                 f"{', '.join(cams_)} liefert keine Gegenstandsanalyse).")
    return text
