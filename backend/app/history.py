"""Zeitmaschine (B1): Bild-Embeddings speichern, Textsuche mit Episoden, Filmstreifen, Statistik, Aufbewahrung.

Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.G. Verdrahtung:
- `main.py` registriert `handle_worker_frame` für `vg/internal/history/{camera_id}` (Track-Thread) und ruft
  `tick()` jede Sekunde. Router `routers/history.py`, Aufbewahrung `retention(now)` aus `retention.py`.
- Datenbank: `models.HistoryFrame` (halfvec(768), nur mit pgvector – `db.vector_available()`).
- Textkodierung: `embed_client.request("text", text=q, model="siglip2", timeout=5)`; die Anfrage wird zusätzlich
  ins Englische übertragen (`history_query.expand`) und je Bild die bessere Ähnlichkeit genommen.

Score 0..100: SigLIP-Kosinus zwischen Text und Bild liegt praktisch zwischen ≈ −0,05 (passt nicht) und ≈ 0,20
(passt sehr gut); `score()` bildet [SIM_LO, SIM_HI] linear auf 0..100 ab (Kalibrierung siehe CONTRACT.md W3.G).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
from sqlalchemy import delete, func, select, text, update

from . import settings_store
from .config import config
from .db import SessionLocal, vector_available
from .models import HISTORY_DIM, Camera, HistoryFrame
from .util import from_unix, safe_remove

log = logging.getLogger("vg.history")

SIM_LO = 0.0          # Kosinus → Score 0
SIM_HI = 0.20         # Kosinus → Score 100
REL_DELTA = 0.03      # Bilder einer Kamera gehören zu Zeiträumen, wenn ihr Kosinus ≥ bester Kosinus − REL_DELTA
CANDIDATES = 400      # Trefferkandidaten je Anfragevektor (HNSW, iterativer Scan bei Filtern)
EF_SEARCH = 200
TEXT_TIMEOUT_S = 5.0
LOAD_RETRY_S = 12.0   # „Modell lädt noch“ (erste Suche nach Leerlauf): so lange erneut versuchen
_STATS_TTL = 60.0

_stats_cache: dict[str, Any] = {"ts": 0.0, "bytes": 0}
_lock = threading.Lock()


class SearchUnavailable(Exception):
    """Textkodierung nicht möglich (Worker offline, Modell lädt, Fehler) → REST 503."""


def enabled() -> bool:
    """Zeitmaschine nutzbar (pgvector aktiv)."""
    return vector_available()


def model_key() -> str:
    return str(settings_store.get().get("history_model") or "siglip2-b16-256")


def score(sim: float) -> int:
    return int(round(100 * max(0.0, min(1.0, (sim - SIM_LO) / (SIM_HI - SIM_LO)))))


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _url(path: Optional[str]) -> Optional[str]:
    return f"/media/{path}" if path else None


# ============================================================================ Worker → DB
def _decode(v: dict) -> Optional[list[float]]:
    import base64

    try:
        arr = np.frombuffer(base64.b64decode(v.get("vec_b64") or ""), dtype="<f2").astype(np.float32)
    except Exception:  # noqa: BLE001
        return None
    if arr.shape[0] != HISTORY_DIM or not np.all(np.isfinite(arr)):
        return None
    return arr.tolist()


def handle_worker_frame(camera_id: Optional[int], msg: dict[str, Any]) -> None:
    """`vg/internal/history/{camera_id}`: Vektoren speichern bzw. `last_ts` verlängern."""
    if not enabled():
        return
    cid = camera_id if camera_id is not None else msg.get("camera_id")
    ts = from_unix(msg.get("ts"))
    if cid is None or ts is None:
        return
    cid = int(cid)
    model = str(msg.get("model") or model_key())[:60]
    with SessionLocal() as s:
        if msg.get("extend"):
            last = s.scalar(select(func.max(HistoryFrame.ts)).where(HistoryFrame.camera_id == cid,
                                                                     HistoryFrame.model == model))
            if last is None or last > ts:
                return
            s.execute(update(HistoryFrame)
                      .where(HistoryFrame.camera_id == cid, HistoryFrame.ts == last, HistoryFrame.last_ts < ts)
                      .values(last_ts=ts))
            s.commit()
            return
        if s.get(Camera, cid) is None:       # Kamera inzwischen gelöscht → Vorschau verwerfen
            safe_remove(msg.get("thumb_path"))
            return
        rows = []
        for v in msg.get("vectors") or []:
            vec = _decode(v)
            if vec is None:
                log.warning("Zeitmaschine: Vektor von Kamera %s ungültig (dim ≠ %d) – verworfen", cid, HISTORY_DIM)
                continue
            bbox = v.get("bbox")
            rows.append(HistoryFrame(camera_id=cid, ts=ts, last_ts=ts, tile=int(v.get("tile") or 0),
                                     bbox=bbox if isinstance(bbox, list) and len(bbox) == 4 else None,
                                     embedding=vec, thumb_path=msg.get("thumb_path"), model=model))
        if not rows:
            safe_remove(msg.get("thumb_path"))
            return
        s.add_all(rows)
        s.commit()


def drop_camera(camera_id: int) -> int:
    """Kamera gelöscht: Zeilen und Vorschauen entfernen."""
    if not enabled():
        return 0
    return delete_frames(camera_id=camera_id)


# ============================================================================ Suche
def encode_query(q: str) -> tuple[list[str], list[list[float]], str]:
    """→ (Anfragetexte, Vektoren, Modellname des Workers). Wirft SearchUnavailable."""
    from .embed_client import EmbedError, EmbedUnavailable, request
    from .history_query import expand

    st = settings_store.get()
    texts = expand(q, st.get("inventory_vocabulary") or [], st.get("inventory_synonyms") or {})
    vecs: list[list[float]] = []
    model = ""
    used: list[str] = []
    deadline = time.time() + LOAD_RETRY_S
    for t in texts:
        while True:
            try:
                res = request("text", text=t, model="siglip2", timeout=TEXT_TIMEOUT_S)
                break
            except EmbedUnavailable as e:
                raise SearchUnavailable("Textsuche derzeit nicht verfügbar") from e
            except EmbedError as e:
                if "lädt" in str(e) and time.time() < deadline:
                    time.sleep(1.0)
                    continue
                raise SearchUnavailable(f"Textsuche derzeit nicht verfügbar ({e})") from e
        if len(res.embedding) != HISTORY_DIM:
            raise SearchUnavailable(f"Textsuche: unerwartete Vektorlänge {len(res.embedding)}")
        vecs.append(res.embedding)
        used.append(t)
        model = res.model
    return used, vecs, model


def _candidates(s, vec: list[float], model: str, cam_ids: Optional[list[int]], since: Optional[datetime],
                until: Optional[datetime]) -> list[tuple]:
    from .vector import to_pg

    where = ["model = :model"]
    params: dict[str, Any] = {"v": to_pg(vec), "model": model, "k": CANDIDATES}
    if cam_ids is not None:
        where.append("camera_id = ANY(:cids)")
        params["cids"] = cam_ids
    if since is not None:
        where.append("last_ts >= :since")
        params["since"] = since
    if until is not None:
        where.append("ts <= :until")
        params["until"] = until
    s.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    s.execute(text(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"))
    sql = (f"SELECT id, camera_id, ts, last_ts, tile, bbox, thumb_path, "
           f"embedding <=> CAST(:v AS halfvec({HISTORY_DIM})) AS dist FROM history_frames "
           f"WHERE {' AND '.join(where)} ORDER BY embedding <=> CAST(:v AS halfvec({HISTORY_DIM})) LIMIT :k")
    return list(s.execute(text(sql), params).all())


def search(q: str, *, camera_id: Optional[int] = None, room: Optional[str] = None,
           since: Optional[datetime] = None, until: Optional[datetime] = None, limit: int = 10) -> dict:
    texts, vecs, _model_name = encode_query(q)
    key = model_key()
    with SessionLocal() as s:
        cams = {c.id: {"name": c.name, "room": c.room, "interval": float(c.history_interval_s or 30)}
                for c in s.scalars(select(Camera)).all()}
        cam_ids: Optional[list[int]] = None
        if camera_id is not None:
            cam_ids = [camera_id]
        if room:
            r = room.strip().lower()
            ids = [i for i, c in cams.items() if (c["room"] or "").strip().lower() == r]
            cam_ids = [i for i in (cam_ids if cam_ids is not None else ids) if i in ids]
        if cam_ids is not None and not cam_ids:
            return {"query": q, "queries": texts, "model": key, "items": []}
        best: dict[int, tuple] = {}            # row id → (sim, row)
        for vec in vecs:
            for row in _candidates(s, vec, key, cam_ids, since, until):
                sim = 1.0 - float(row.dist)
                cur = best.get(row.id)
                if cur is None or sim > cur[0]:
                    best[row.id] = (sim, row)
        s.rollback()                           # SET LOCAL beenden
    episodes = _episodes(best.values(), cams)
    episodes.sort(key=lambda e: (-e["_sim"], e["start_ts"]))
    items = []
    for e in episodes[:limit]:
        e.pop("_sim")
        items.append(e)
    return {"query": q, "queries": texts, "model": key, "items": items}


def _episodes(hits, cams: dict[int, dict]) -> list[dict]:
    """Treffer derselben Kamera mit Abstand ≤ 2 × Intervall zu Zeiträumen zusammenfassen."""
    by_cam: dict[int, list[tuple]] = {}
    for sim, row in hits:
        by_cam.setdefault(row.camera_id, []).append((sim, row))
    out: list[dict] = []
    for cid, lst in by_cam.items():
        cam = cams.get(cid)
        # Vertrag: Abstand ≤ 2 × Intervall; + ½ Intervall Toleranz für Taktschwankungen (Frame-Tap, Inferenz)
        gap = timedelta(seconds=2.5 * (cam["interval"] if cam else 30.0))
        # nur Bilder nahe am besten Treffer der Kamera bilden Zeiträume (sonst verschmilzt alles Gespeicherte
        # einer ruhigen Kamera zu einem einzigen Zeitraum)
        top = max(sim for sim, _r in lst)
        lst = [x for x in lst if x[0] >= top - REL_DELTA]
        lst.sort(key=lambda x: (x[1].ts, x[1].tile))
        cur: Optional[dict] = None
        for sim, row in lst:
            if cur is not None and row.ts - cur["_end"] <= gap:
                cur["_end"] = max(cur["_end"], row.last_ts)
                cur["_frames"].add(row.ts)
                if sim > cur["_sim"]:
                    cur["_sim"], cur["_best"] = sim, row
                continue
            if cur is not None:
                out.append(_episode(cur, cam, cid))
            cur = {"_start": row.ts, "_end": row.last_ts, "_frames": {row.ts}, "_sim": sim, "_best": row}
        if cur is not None:
            out.append(_episode(cur, cam, cid))
    return out


def _episode(e: dict, cam: Optional[dict], cid: int) -> dict:
    b = e["_best"]
    return {"camera_id": cid, "camera_name": cam["name"] if cam else f"Kamera {cid}",
            "room": cam["room"] if cam else None,
            "start_ts": _iso(e["_start"]), "end_ts": _iso(e["_end"]), "best_ts": _iso(b.ts),
            "score": score(e["_sim"]), "similarity": round(e["_sim"], 4),
            "tile_bbox": b.bbox if b.tile else None, "thumb_url": _url(b.thumb_path),
            "frames": len(e["_frames"]), "_sim": e["_sim"]}


# ============================================================================ Filmstreifen, Statistik
def frames(camera_id: int, around: datetime, span_min: int = 10) -> dict:
    lo, hi = around - timedelta(minutes=span_min), around + timedelta(minutes=span_min)
    with SessionLocal() as s:
        rows = s.execute(select(HistoryFrame.ts, HistoryFrame.last_ts, HistoryFrame.thumb_path)
                         .where(HistoryFrame.camera_id == camera_id, HistoryFrame.tile == 0,
                                HistoryFrame.last_ts >= lo, HistoryFrame.ts <= hi)
                         .order_by(HistoryFrame.ts).limit(500)).all()
    return {"items": [{"ts": _iso(ts), "last_ts": _iso(lts), "thumb_url": _url(p)} for ts, lts, p in rows]}


def _thumb_bytes() -> int:
    now = time.time()
    with _lock:
        if now - _stats_cache["ts"] < _STATS_TTL:
            return int(_stats_cache["bytes"])
    total = 0
    root = os.path.join(config.media_dir, "history")
    for dirpath, _d, files in os.walk(root):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    with _lock:
        _stats_cache.update(ts=now, bytes=total)
    return total


def stats() -> dict:
    if not enabled():
        return {"enabled": False, "frames": 0, "vectors": 0, "thumb_bytes": 0, "oldest": None, "by_camera": {},
                "model": model_key()}
    with SessionLocal() as s:
        vectors = s.scalar(select(func.count()).select_from(HistoryFrame)) or 0
        by_cam = dict(s.execute(select(HistoryFrame.camera_id, func.count()).where(HistoryFrame.tile == 0)
                                .group_by(HistoryFrame.camera_id)).all())
        oldest = s.scalar(select(func.min(HistoryFrame.ts)))
    return {"enabled": True, "frames": int(sum(by_cam.values())), "vectors": int(vectors),
            "thumb_bytes": _thumb_bytes(), "oldest": _iso(oldest),
            "by_camera": {str(k): int(v) for k, v in by_cam.items()}, "model": model_key()}


# ============================================================================ Löschen, Aufbewahrung
def delete_frames(camera_id: Optional[int] = None, before: Optional[datetime] = None) -> int:
    """Zeilen (und ihre Vorschauen) löschen; ohne Filter alles. → Anzahl gelöschter Bilder (Kachel 0)."""
    conds = []
    if camera_id is not None:
        conds.append(HistoryFrame.camera_id == camera_id)
    if before is not None:
        conds.append(HistoryFrame.last_ts < before)
    with SessionLocal() as s:
        res = s.execute(delete(HistoryFrame).where(*conds)
                        .returning(HistoryFrame.tile, HistoryFrame.thumb_path)).all()
        s.commit()
    paths = {p for _t, p in res if p}
    for p in paths:
        safe_remove(p)
    with _lock:
        _stats_cache["ts"] = 0.0
    return sum(1 for t, _p in res if t == 0)


def tick() -> None:
    """Jede Sekunde vom Backend-Ticker – derzeit nichts zu tun (Aufbewahrung stündlich über retention)."""
    return None


def retention(now: datetime) -> int:
    """Stündlich: Zeilen, deren `last_ts` älter als `retention_days_history` ist, samt Vorschau löschen; dazu
    verwaiste Vorschaudateien unter history/ (z. B. von gelöschten Kameras oder ohne Datenbank)."""
    days = int(settings_store.get().get("retention_days_history") or 14)
    cutoff = now - timedelta(days=days)
    n = delete_frames(before=cutoff) if enabled() else 0
    keep: set[str] = set()
    if enabled():
        with SessionLocal() as s:
            keep = {p for p in s.scalars(select(HistoryFrame.thumb_path).distinct()
                                         .where(HistoryFrame.ts < cutoff + timedelta(days=1))).all() if p}
    keep_abs = {os.path.realpath(os.path.join(config.media_dir, p)) for p in keep}
    root = os.path.join(config.media_dir, "history")
    limit = cutoff.timestamp()
    files = 0
    if os.path.isdir(root):
        for dirpath, _d, filenames in os.walk(root, topdown=False):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    if os.path.getmtime(fp) < limit and os.path.realpath(fp) not in keep_abs:
                        os.remove(fp)
                        files += 1
                except OSError:
                    pass
            if dirpath != root:
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass
    if n or files:
        log.info("Aufräumen Zeitmaschine: %d Bilder (Zeilen) und %d verwaiste Dateien entfernt", n, files)
    return n
