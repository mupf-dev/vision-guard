"""Person über mehrere Kameras (C2): RAM-Galerie für ReID und Anwesenheit (`GET /presence`).

Vertrag: CONTRACT.md „Nachträge: Welle 4 – C2“. Kernpunkte:
- **Galerie nur im RAM** (kein DB-Eintrag, keine Datei, kein Log): `presence_key → Eintrag` mit höchstens
  `MAX_EMBEDDINGS` OSNet-Embeddings, verknüpften Tracks, Identität und letzter Kamera. Einträge ohne aktiven Track
  werden `reid_ttl_min` nach dem letzten Sehen samt Embeddings verworfen; ein Backend-Neustart leert alles.
- Track-Schritte kommen über `events.on_track` (Personen, Hunde, Katzen): jeder Track gehört genau einem Eintrag.
  Ohne ReID: Eintrag je Identität (Gesicht/Tier) bzw. je Track („unbekannt“).
- `POST /api/internal/reid` (Worker, nur `reid_enabled`-Kameras): Abgleich per Kosinus (Maximum über die
  Embeddings eines Eintrags). Verknüpft wird nur mit Einträgen **ohne gleichzeitig aktiven anderen Track** und ohne
  widersprechende Gesichts-Identität; ab `reid_match_threshold`. Ein Track wird nur verknüpft, solange er allein in
  seinem Eintrag steht (kein Hin- und Herspringen).
- Gesicht hat Vorrang: Eine Gesichts-Identität eines Tracks setzt die Identität des Eintrags (gleiche Identität in
  zwei Einträgen → zusammenführen). Über ReID wird die Identität nur weitergegeben (Antwort `identity`), wenn
  `score ≥ reid_match_threshold + 0,1` und `reid_assign_identity` an ist; sie überschreibt nie eine eigene.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from . import registry, settings_store

log = logging.getLogger("vg.reid")

PRESENCE_LABELS = ("person", "dog", "cat")
MAX_EMBEDDINGS = 10
IDENTITY_MARGIN = 0.10          # ReID gibt eine Identität erst ab Schwelle + 0,1 weiter (Vertrag)
WS_MIN_GAP_S = 2.0              # reine Zonen-/Zeit-Updates je Eintrag höchstens alle 2 s an den WebSocket
TRAIL_MAX = 8                   # Verlauf je Eintrag (letzte Tracks)


@dataclass
class TrackRef:
    camera_id: int
    track_id: int
    label: str
    event_id: Optional[str] = None
    first_seen: float = 0.0
    last_seen: float = 0.0
    active: bool = True
    zones: list[str] = field(default_factory=list)
    own_identity: Optional[dict[str, Any]] = None       # eigene Identität (Gesicht/Tier), nicht über ReID
    link_score: Optional[float] = None                  # Score der ReID-Verknüpfung (None = nicht verknüpft)
    reid: bool = False                                  # mind. ein Embedding von diesem Track


@dataclass
class Entry:
    key: str
    label: str
    created: float
    last_seen: float
    identity: Optional[dict[str, Any]] = None           # {"id", "kind"} – nur aus Gesicht/Tier, nie aus ReID
    tracks: dict[tuple[int, int], TrackRef] = field(default_factory=dict)
    # (Embedding, Qualität, ts, Track) – nur RAM
    embeddings: list[tuple[np.ndarray, float, float, tuple[int, int]]] = field(default_factory=list)
    last_ws: float = 0.0
    last_sig: Any = None


class Gallery:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.entries: dict[str, Entry] = {}
        self.track_key: dict[tuple[int, int], str] = {}
        self.stats = {"requests": 0, "linked": 0, "new": 0, "identity_given": 0, "merged": 0, "expired": 0}
        self._last_gc = 0.0

    # ---------------------------------------------------------------- Hilfen
    @staticmethod
    def _settings() -> tuple[float, float, bool]:
        st = settings_store.get()
        return (float(st.get("reid_match_threshold", 0.70)), float(st.get("reid_ttl_min", 60)) * 60.0,
                bool(st.get("reid_assign_identity", True)))

    def _new_entry(self, label: str, now: float) -> Entry:
        key = "p-" + secrets.token_hex(5)
        e = Entry(key=key, label=label, created=now, last_seen=now)
        self.entries[key] = e
        return e

    def _by_identity(self, iid: int) -> Optional[Entry]:
        for e in self.entries.values():
            if e.identity and e.identity.get("id") == iid:
                return e
        return None

    def _move_track(self, tk: tuple[int, int], src: Entry, dst: Entry) -> None:
        ref = src.tracks.pop(tk, None)
        if ref is not None:
            dst.tracks[tk] = ref
            dst.last_seen = max(dst.last_seen, ref.last_seen)
        moved = [x for x in src.embeddings if x[3] == tk]
        if moved:
            src.embeddings = [x for x in src.embeddings if x[3] != tk]
            dst.embeddings.extend(moved)
            self._prune(dst)
        self.track_key[tk] = dst.key

    def _merge(self, src: Entry, dst: Entry) -> None:
        """src in dst aufgehen lassen (gleiche Person laut Gesicht)."""
        for tk in list(src.tracks):
            self._move_track(tk, src, dst)
        dst.embeddings.extend(src.embeddings)
        src.embeddings = []
        self._prune(dst)
        dst.last_seen = max(dst.last_seen, src.last_seen)
        self.entries.pop(src.key, None)
        self.stats["merged"] += 1
        _broadcast_deleted(src.key, merged_into=dst.key)

    @staticmethod
    def _prune(e: Entry) -> None:
        if len(e.embeddings) > MAX_EMBEDDINGS:
            # niedrigste Qualität zuerst, bei Gleichstand die ältesten
            e.embeddings.sort(key=lambda x: (x[1], x[2]), reverse=True)
            del e.embeddings[MAX_EMBEDDINGS:]

    def _drop_if_empty(self, e: Entry) -> None:
        if not e.tracks and not e.embeddings and e.identity is None:
            self.entries.pop(e.key, None)
            _broadcast_deleted(e.key)

    def _ref(self, camera_id: int, track_id: int, label: str, now: float) -> tuple[TrackRef, Entry]:
        tk = (camera_id, track_id)
        key = self.track_key.get(tk)
        e = self.entries.get(key) if key else None
        if e is None:
            e = self._new_entry(label, now)
            self.track_key[tk] = e.key
        ref = e.tracks.get(tk)
        if ref is None:
            ref = e.tracks[tk] = TrackRef(camera_id=camera_id, track_id=track_id, label=label, first_seen=now,
                                          last_seen=now)
        return ref, e

    # ---------------------------------------------------------------- Track-Hook (Track-Thread)
    def on_track(self, hook) -> None:
        if hook.label not in PRESENCE_LABELS:
            return
        now = time.time()
        tr, msg = hook.track, hook.msg or {}
        with self.lock:
            ref, e = self._ref(hook.camera_id, hook.track_id, hook.label, now)
            ref.event_id = hook.event_id
            ref.first_seen = float(tr.first_seen or ref.first_seen)
            seen = msg.get("last_seen") or msg.get("ts")
            try:
                ref.last_seen = max(ref.last_seen, float(seen)) if seen else now
            except (TypeError, ValueError):
                ref.last_seen = now
            ref.zones = list(tr.zones or [])
            if hook.phase == "end":
                ref.active = False
            e.last_seen = max(e.last_seen, ref.last_seen)
            # eigene Identität (Gesicht/Tier) – über ReID weitergegebene zählt nicht
            ident = tr.identity or msg.get("identity")
            src = (tr.attributes or {}).get("identity_source")
            if ident and ident.get("id") is not None and src != "reid":
                e = self._apply_own_identity(ref, e, {"id": int(ident["id"]), "kind": ident.get("kind")})
            self._maybe_broadcast(e, force=hook.phase in ("start", "end"))

    def _apply_own_identity(self, ref: TrackRef, e: Entry, ident: dict[str, Any]) -> Entry:
        tk = (ref.camera_id, ref.track_id)
        if ref.own_identity and ref.own_identity.get("id") == ident["id"]:
            return e
        ref.own_identity = ident
        if e.identity is None:
            other = self._by_identity(ident["id"])
            if other is not None and other is not e:
                if len(e.tracks) == 1:
                    self._move_track(tk, e, other)     # Track allein → in den Eintrag der Identität
                    self._drop_if_empty(e)
                    self._maybe_broadcast(other, force=True)
                    return other
                self._merge(e, other)                  # verknüpfte Gruppe gehört zu dieser Identität
                self._maybe_broadcast(other, force=True)
                return other
            e.identity = ident
            return e
        if e.identity.get("id") == ident["id"]:
            return e
        # Widerspruch: Eintrag hat eine andere Identität → Track (mit seinen Embeddings) herauslösen
        dst = self._by_identity(ident["id"])
        if dst is None:
            dst = self._new_entry(ref.label, time.time())
            dst.identity = ident
        log.info("Anwesenheit: Track %s/%s widerspricht Identität des Eintrags – getrennt", ref.camera_id,
                 ref.track_id)
        ref.link_score = None
        self._move_track(tk, e, dst)
        self._drop_if_empty(e)
        if e.key in self.entries:
            self._maybe_broadcast(e, force=True)
        self._maybe_broadcast(dst, force=True)
        return dst

    # ---------------------------------------------------------------- POST /api/internal/reid
    def match(self, camera_id: int, track_id: int, embedding: list[float], quality: float, ts: float,
              event_id: Optional[str] = None) -> dict[str, Any]:
        thr, ttl, assign = self._settings()
        v = np.asarray(embedding, dtype=np.float32)
        n = float(np.linalg.norm(v))
        if v.ndim != 1 or v.size < 64 or not np.isfinite(n) or n <= 0:
            raise ValueError("Embedding ungültig")
        v = v / n
        now = time.time()
        tk = (camera_id, track_id)
        with self.lock:
            self.stats["requests"] += 1
            self._gc(now, ttl)
            ref, cur = self._ref(camera_id, track_id, "person", now)
            if event_id and not ref.event_id:
                ref.event_id = event_id
            ref.reid = True
            ref.last_seen = max(ref.last_seen, ts or now)
            own = ref.own_identity
            best, best_s = None, -1.0
            alone = len(cur.tracks) == 1
            if alone:
                for e in self.entries.values():
                    if e is cur or e.label != "person" or not e.embeddings:
                        continue
                    if any(t.active for k, t in e.tracks.items() if k != tk):
                        continue                       # gleichzeitig aktiv → andere Person
                    if own and e.identity and e.identity.get("id") != own.get("id"):
                        continue                       # widersprechende Gesichts-Identität
                    if cur.identity and e.identity and e.identity.get("id") != cur.identity.get("id"):
                        continue
                    D = np.stack([x[0] for x in e.embeddings])
                    s = float((D @ v).max())
                    if s > best_s:
                        best, best_s = e, s
            linked = False
            if best is not None and best_s >= thr:
                # verknüpfen: Track (und ggf. seine Identität) in den gefundenen Eintrag
                if cur.identity and best.identity is None:
                    best.identity = cur.identity
                self._move_track(tk, cur, best)
                if not cur.tracks:
                    self.entries.pop(cur.key, None)
                    _broadcast_deleted(cur.key, merged_into=best.key)
                ref.link_score = round(best_s, 4)
                cur = best
                linked = True
                self.stats["linked"] += 1
            elif not cur.embeddings:
                self.stats["new"] += 1
            cur.embeddings.append((v, float(quality or 0.0), float(ts or now), tk))
            self._prune(cur)
            cur.last_seen = max(cur.last_seen, ref.last_seen)
            is_new = len(cur.tracks) == 1 and not linked
            out: dict[str, Any] = {"presence_key": cur.key, "identity_id": None, "score": round(max(best_s, 0.0), 4),
                                   "is_new": is_new, "identity": None, "identity_source": None}
            if cur.identity:
                out["identity_id"] = int(cur.identity["id"])
                if own and own.get("id") == cur.identity.get("id"):
                    out["identity_source"] = own.get("kind") or "face"
                elif (assign and not own and ref.link_score is not None
                      and ref.link_score >= thr + IDENTITY_MARGIN and cur.identity.get("kind") == "face"):
                    ident = _identity_summary(int(cur.identity["id"]))
                    if ident:
                        out["identity"] = ident
                        out["identity_source"] = "reid"
                        self.stats["identity_given"] += 1
            self._maybe_broadcast(cur, force=linked or is_new)
            return out

    # ---------------------------------------------------------------- Aufräumen
    def _gc(self, now: float, ttl: float) -> None:
        if now - self._last_gc < 5.0:
            return
        self._last_gc = now
        reid_cams = {c["id"] for c in registry.all() if c.get("reid_enabled")}
        for e in list(self.entries.values()):
            # Embeddings von Kameras, deren ReID abgeschaltet wurde, sofort verwerfen
            if e.embeddings and any(x[3][0] not in reid_cams for x in e.embeddings):
                e.embeddings = [x for x in e.embeddings if x[3][0] in reid_cams]
            for tk, t in list(e.tracks.items()):
                if t.active and now - t.last_seen > 120:
                    t.active = False                  # Track-Ende verpasst (Worker weg)
                if not t.active and now - t.last_seen > ttl:
                    e.tracks.pop(tk, None)
                    self.track_key.pop(tk, None)
            if not any(t.active for t in e.tracks.values()) and now - e.last_seen > ttl:
                for tk in list(e.tracks):
                    self.track_key.pop(tk, None)
                self.entries.pop(e.key, None)
                self.stats["expired"] += 1
                _broadcast_deleted(e.key)

    def tick(self) -> None:
        _, ttl, _ = self._settings()
        with self.lock:
            self._gc(time.time(), ttl)

    def clear(self) -> int:
        with self.lock:
            keys = list(self.entries)
            self.entries.clear()
            self.track_key.clear()
        for k in keys:
            _broadcast_deleted(k)
        return len(keys)

    # ---------------------------------------------------------------- Ausgabe
    def _current(self, e: Entry) -> Optional[TrackRef]:
        if not e.tracks:
            return None
        act = [t for t in e.tracks.values() if t.active]
        pool = act or list(e.tracks.values())
        return max(pool, key=lambda t: (t.last_seen, t.first_seen))

    def _sig(self, e: Entry) -> Any:
        t = self._current(e)
        return (e.identity and e.identity.get("id"), t and (t.camera_id, t.active, tuple(t.zones), t.track_id))

    def _maybe_broadcast(self, e: Entry, force: bool = False) -> None:
        sig = self._sig(e)
        now = time.time()
        if not force and sig == e.last_sig and now - e.last_ws < 30:
            return
        if not force and now - e.last_ws < WS_MIN_GAP_S:
            return
        e.last_sig, e.last_ws = sig, now
        item = self.item(e, names=None)
        if item is not None:
            _broadcast({"type": "presence", "presence": item})

    def item(self, e: Entry, names: Optional[dict[int, dict]] = None,
             thumbs: Optional[dict[str, Optional[str]]] = None) -> Optional[dict[str, Any]]:
        from .util import from_unix
        t = self._current(e)
        if t is None:
            return None
        cam = registry.get(t.camera_id) or {}
        ident = None
        if e.identity:
            iid = int(e.identity["id"])
            ident = (names or {}).get(iid) if names is not None else _identity_summary(iid)
            if names is not None and ident is None:
                ident = None          # Identität inzwischen gelöscht
        if ident and t.own_identity and t.own_identity.get("id") == ident.get("id"):
            source, conf = (t.own_identity.get("kind") or "face"), 1.0
        elif ident and t.link_score is not None:
            source, conf = "reid", float(t.link_score)
        elif ident:
            # Gründungs-Track eines Eintrags, dessen Identität ein später verknüpfter Track mitbrachte
            scores = [x.link_score for x in e.tracks.values() if x.link_score is not None]
            source, conf = "reid", float(max(scores) if scores else 0.0)
        else:
            source, conf = ("reid" if t.link_score is not None else "none"), float(t.link_score or 0.0)
        zone_names = cam.get("zones") or {}
        trail = sorted(e.tracks.values(), key=lambda x: x.first_seen, reverse=True)[:TRAIL_MAX]
        thumb = (thumbs or {}).get(t.event_id) if thumbs is not None else None
        return {
            "presence_key": e.key, "label": e.label, "identity": ident, "source": source,
            "confidence": round(conf, 3), "camera_id": t.camera_id, "camera_name": cam.get("name"),
            "room": cam.get("room"), "zones": list(t.zones), "zone_names": [zone_names.get(z, z) for z in t.zones],
            "active": bool(t.active), "since": _iso(from_unix(t.first_seen)), "last_seen": _iso(from_unix(t.last_seen)),
            "event_id": t.event_id, "thumb_url": f"/media/{thumb}" if thumb else None,
            "trail": [{"camera_id": x.camera_id, "camera_name": (registry.get(x.camera_id) or {}).get("name"),
                       "since": _iso(from_unix(x.first_seen)), "last_seen": _iso(from_unix(x.last_seen)),
                       "active": bool(x.active), "event_id": x.event_id,
                       "via": ("face" if x.own_identity else ("reid" if x.link_score is not None else "track"))}
                      for x in trail],
        }

    def snapshot(self, minutes: float) -> list[Entry]:
        now = time.time()
        with self.lock:
            out = [e for e in self.entries.values()
                   if e.tracks and (any(t.active for t in e.tracks.values()) or now - e.last_seen <= minutes * 60)]
            return list(out)

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {"entries": len(self.entries),
                    "active": sum(1 for e in self.entries.values() if any(t.active for t in e.tracks.values())),
                    "embeddings": sum(len(e.embeddings) for e in self.entries.values()), **self.stats}


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _identity_summary(iid: int) -> Optional[dict[str, Any]]:
    from .db import SessionLocal
    from .models import Identity
    from .serializers import identity_summary
    with SessionLocal() as s:
        return identity_summary(s.get(Identity, iid))


def _broadcast(msg: dict[str, Any]) -> None:
    from .ws import hub
    hub.broadcast(msg)


def _broadcast_deleted(key: str, merged_into: Optional[str] = None) -> None:
    p: dict[str, Any] = {"presence_key": key, "deleted": True}
    if merged_into:
        p["merged_into"] = merged_into
    _broadcast({"type": "presence", "presence": p})


gallery = Gallery()
_unhook = None


def list_presence(minutes: float = 60, label: Optional[str] = None) -> list[dict[str, Any]]:
    """GET /presence: Einträge mit aktivem Track oder zuletzt innerhalb von `minutes` gesehen."""
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import Event, Identity
    from .serializers import identity_summary
    entries = gallery.snapshot(minutes)
    if label:
        labels = {x.strip() for x in label.split(",") if x.strip()}
        entries = [e for e in entries if e.label in labels]
    ids = {int(e.identity["id"]) for e in entries if e.identity}
    with gallery.lock:
        evs = {t.event_id for e in entries for t in [gallery._current(e)] if t and t.event_id}
    with SessionLocal() as s:
        names = {i.id: identity_summary(i) for i in s.scalars(select(Identity).where(Identity.id.in_(ids)))} if ids else {}
        thumbs = dict(s.execute(select(Event.id, Event.thumb_path).where(Event.id.in_(evs))).all()) if evs else {}
    with gallery.lock:
        items = [gallery.item(e, names=names, thumbs=thumbs) for e in entries]
    items = [x for x in items if x]
    items.sort(key=lambda x: (not x["active"], -(_ts(x["last_seen"]))))
    return items


def _ts(iso: Optional[str]) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else 0.0
    except ValueError:
        return 0.0


def _on_track(hook) -> None:
    gallery.on_track(hook)


def startup() -> None:
    global _unhook
    from . import events   # zyklischen Import vermeiden
    if _unhook is None:
        _unhook = events.on_track(_on_track)
    log.info("Anwesenheit/ReID aktiv (Galerie nur im RAM)")


def tick() -> None:
    gallery.tick()
