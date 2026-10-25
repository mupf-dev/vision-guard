"""Identitäten 07 (Paket D): Nachtlauf – Fragmente bündeln, Galerie kuratieren, Schwellen je Person, Aufräumen.

CONTRACT ID.H. Ablauf:
- `tick()` (Backend-Ticker, jede Sekunde, selbst gedrosselt): fällig ab `nightly_time` (Ortszeit), ein Lauf je
  lokalem Tag (`nightly_reports.period_key = "nightly:YYYY-MM-DD"`), Nachholen bis 6 h; `nightly_mode = "off"` → nichts.
- `start(mode, trigger, apply_from=None, idx=None)` → NightlyReport-ID; Lauf in eigenem Thread, höchstens einer
  gleichzeitig (`RunningError` → 409).
- `status()` → `{mode, time, running, running_report_id, last, next_run}`.
- Modus `preview` (Standard dieser Installation): alles nur berechnen und als Aktionen speichern – **keine**
  Änderung an Identitäten, Bildern, Ereignissen oder Rückfragen.
- Modus `apply`: Merges nur Unbenannt → Ziel (nie zwei Benannte) über `identity_bulk.bulk_merge(actor="nightly")`
  (Verlauf, rücksetzbar), Rückfragen über `review.create(kind="identity_match", …)`, Kuratieren/Schwellen/Aufräumen.
- Anwenden einer Vorschau (`trigger="apply"`): genau deren Aktionen (Auswahl per `idx`), jede neu geprüft.

Entscheidungen (Paket D, dokumentiert in docs/plan/07-identitaeten-fortschritt.md):
- Score Fragment → Kandidat wie ID.F: Mittel der besten 3 Bild-Ähnlichkeiten des Kandidaten zum **Mittel** der
  Fragment-Bilder (entspricht der endgültigen Track-Zuordnung).
- Bündel per Union-Find über sichere Kanten. Ein Bündel mit mehr als einer „festen“ Identität (benannt oder mit
  Lernsperre) wird nicht automatisch zusammengeführt (→ Rückfragen). Gegen Ketten-Drift muss jede Quelle auch
  direkt zum Ziel mindestens `nightly_review_min` erreichen, sonst Rückfrage statt Merge.
- Kuratieren und Aufräumen lassen Identitäten mit Lernsperre unangetastet; manuelle Bilder werden nie negativ
  markiert oder entfernt; höchstens 20 % der Bilder einer Person werden in einem Lauf negativ markiert.
- Hinweis (Notification) bei geplanten Läufen und beim Anwenden; ein von Hand gestarteter Probelauf erzeugt keinen
  Hinweis (der Nutzer sieht den Bericht ohnehin in der Oberfläche).
"""
from __future__ import annotations

import logging
import threading
import time as _time
from datetime import date as _date, datetime, time, timedelta, timezone
from typing import Any, Optional

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import face_match, settings_store
from . import identity as ident_svc
from .config import config
from .db import SessionLocal
from .models import Event, Identity, IdentitySample, NightlyReport, utcnow
from .schemas import iso, media_url
from .ws import hub

log = logging.getLogger("vg.nightly")

CHECK_S = 20                  # so oft prüft tick(), ob ein Lauf fällig ist
CATCH_UP = timedelta(hours=6)
STARTUP_GRACE_S = 60          # nach dem Start nichts planen (Worker/MQTT kommen hoch)
RETENTION_S = 3600

CURATE_MIN_SAMPLES = 5        # Kuratieren erst ab so vielen (nicht negativen) Bildern
OUTLIER_DROP = 0.25           # Ausreißer: Ähnlichkeit zum Zentroid > 0,25 unter dem Median (wie select_gallery)
MAX_NEGATIVE_SHARE = 0.20     # höchstens 20 % der Bilder je Lauf negativ markieren
THRESHOLD_MIN_SAMPLES = 8
THRESHOLD_RANGE = (0.25, 0.45)
THRESHOLD_PCT = 10            # Perzentil der eigenen Leave-one-out-Scores …
THRESHOLD_SLACK = 0.05        # … minus Puffer = Schwelle der Person

_run_lock = threading.Lock()
_running_id: Optional[int] = None
_started = _time.monotonic()
_last_check = 0.0
_last_retention = 0.0
_recovered = False


class RunningError(RuntimeError):
    """Es läuft bereits ein Nachtlauf (→ HTTP 409)."""


class ApplyError(RuntimeError):
    """Vorschau nicht anwendbar (→ HTTP 404/409)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------- Zeitplan


def _parse_hm(v: Optional[str]) -> time:
    try:
        h, m = (int(x) for x in (v or "03:00").split(":", 1))
        return time(h % 24, m % 60)
    except ValueError:
        return time(3, 0)


def _local_at(d: _date, t: time) -> datetime:
    return datetime.combine(d, t, config.tz).astimezone(timezone.utc)


def period_key(d: _date) -> str:
    return f"nightly:{d.isoformat()}"


def _due_day(now: datetime, st: dict[str, Any]) -> Optional[_date]:
    t = _parse_hm(st.get("nightly_time"))
    today = now.astimezone(config.tz).date()
    for d in (today, today - timedelta(days=1)):
        at = _local_at(d, t)
        if at <= now < at + CATCH_UP:
            return d
    return None


def next_run(now: Optional[datetime] = None, st: Optional[dict[str, Any]] = None) -> Optional[datetime]:
    st = st or settings_store.get()
    if st.get("nightly_mode", "preview") == "off":
        return None
    now = now or datetime.now(timezone.utc)
    t = _parse_hm(st.get("nightly_time"))
    today = now.astimezone(config.tz).date()
    with SessionLocal() as s:
        have = set(s.scalars(select(NightlyReport.period_key).where(NightlyReport.period_key.in_(
            [period_key(today - timedelta(days=1)), period_key(today)]))).all())
    for d in (today - timedelta(days=1), today, today + timedelta(days=1)):
        at = _local_at(d, t)
        if period_key(d) in have or now >= at + CATCH_UP:
            continue
        return max(at, now)
    return None


def _recover_stale() -> None:
    """Nach einem Neustart: Berichte, die noch auf `running` stehen, als abgebrochen markieren."""
    with SessionLocal() as s:
        rows = s.scalars(select(NightlyReport).where(NightlyReport.status == "running")).all()
        for r in rows:
            if r.id != _running_id:
                r.status, r.error, r.finished_at = "error", "Abgebrochen (Neustart des Backends)", utcnow()
        if rows:
            s.commit()


def _retention() -> None:
    days = int(settings_store.get().get("retention_days_events") or 30)
    with SessionLocal() as s:
        n = s.execute(delete(NightlyReport).where(
            NightlyReport.started_at < utcnow() - timedelta(days=days),
            NightlyReport.status != "running")).rowcount or 0
        s.commit()
    if n:
        log.info("Aufräumen: %d Nachtlauf-Berichte entfernt", n)


def tick() -> None:
    global _last_check, _last_retention, _recovered
    mono = _time.monotonic()
    if not _recovered:
        _recovered = True
        try:
            _recover_stale()
        except Exception:
            log.exception("Nachtlauf: Aufräumen abgebrochener Läufe fehlgeschlagen")
    if mono - _last_retention >= RETENTION_S and mono - _started > 120:
        _last_retention = mono
        try:
            _retention()
        except Exception:
            log.exception("Aufräumen der Nachtlauf-Berichte fehlgeschlagen")
    if mono - _last_check < CHECK_S or mono - _started < STARTUP_GRACE_S:
        return
    _last_check = mono
    st = settings_store.get()
    mode = st.get("nightly_mode", "preview")
    if mode not in ("preview", "apply"):
        return
    d = _due_day(datetime.now(timezone.utc), st)
    if d is None:
        return
    with SessionLocal() as s:
        if s.scalar(select(NightlyReport.id).where(NightlyReport.period_key == period_key(d))) is not None:
            return
    try:
        start(mode, "schedule", period=period_key(d))
    except RunningError:
        pass
    except IntegrityError:   # zweiter Prozess/Takt war schneller
        pass


# ---------------------------------------------------------------- Starten / Status


def summary_json(r: NightlyReport) -> dict[str, Any]:
    return {"id": r.id, "mode": r.mode, "trigger": r.trigger, "status": r.status,
            "started_at": iso(r.started_at), "finished_at": iso(r.finished_at), "error": r.error,
            "applied_report_id": r.applied_report_id, "applied_at": iso(r.applied_at),
            "summary": dict(r.summary or {}), "text": r.text or ""}


def report_json(s: Session, r: NightlyReport) -> dict[str, Any]:
    """Vollständiger Bericht; Vorschaubilder werden für noch existierende Identitäten frisch ermittelt (nach einem
    Merge liegen die Bilder unter dem Ziel)."""
    actions = [dict(a) for a in (r.actions or [])]
    ids: set[int] = set()
    for a in actions:
        for k in ("src_id", "into_id", "identity_id"):
            if a.get(k) is not None:
                ids.add(int(a[k]))
    covers = ident_svc._covers(s, sorted(ids))
    for a in actions:
        for k, ck in (("src_id", "src_cover_url"), ("into_id", "into_cover_url"), ("identity_id", "cover_url")):
            if a.get(k) is not None and int(a[k]) in covers:
                a[ck] = media_url(covers[int(a[k])])
    return {**summary_json(r), "actions": actions}


def status() -> dict[str, Any]:
    st = settings_store.get()
    with SessionLocal() as s:
        last = s.scalars(select(NightlyReport).order_by(NightlyReport.id.desc()).limit(1)).first()
        last_json = summary_json(last) if last else None
    nr = next_run(st=st)
    return {"mode": st.get("nightly_mode", "preview"), "time": st.get("nightly_time", "03:00"),
            "running": _running_id is not None, "running_report_id": _running_id,
            "last": last_json, "next_run": iso(nr) if nr else None}


def list_reports(limit: int = 20, offset: int = 0) -> dict[str, Any]:
    with SessionLocal() as s:
        total = s.scalar(select(func.count()).select_from(NightlyReport)) or 0
        rows = s.scalars(select(NightlyReport).order_by(NightlyReport.id.desc()).limit(limit).offset(offset)).all()
        return {"items": [summary_json(r) for r in rows], "total": int(total)}


def get_report(rid: int) -> Optional[dict[str, Any]]:
    with SessionLocal() as s:
        r = s.get(NightlyReport, rid)
        return report_json(s, r) if r else None


def start(mode: str, trigger: str, apply_from: Optional[int] = None, idx: Optional[list[int]] = None,
          period: Optional[str] = None) -> dict[str, Any]:
    """Legt den Bericht an (`status="running"`) und startet den Lauf im Hintergrund. Rückgabe: Summary-JSON."""
    global _running_id
    if mode not in ("preview", "apply"):
        raise ValueError(f"unbekannter Modus: {mode}")
    if not _run_lock.acquire(blocking=False):
        raise RunningError("Es läuft bereits ein Nachtlauf")
    try:
        with SessionLocal() as s:
            src: Optional[NightlyReport] = None
            if apply_from is not None:
                src = s.get(NightlyReport, apply_from)
                if src is None:
                    raise ApplyError(404, "Bericht nicht gefunden")
                if src.mode != "preview" or src.status != "done":
                    raise ApplyError(409, "Nur ein fertiger Probelauf kann angewandt werden")
                if src.applied_at is not None:
                    raise ApplyError(409, "Dieser Probelauf wurde bereits angewandt")
                src.applied_at = utcnow()   # sofort sperren: kein zweites Anwenden derselben Vorschau
            r = NightlyReport(period_key=period, mode=mode, trigger=trigger, status="running", started_at=utcnow(),
                              summary={}, actions=[], text="", applied_report_id=apply_from)
            s.add(r)
            s.commit()
            s.refresh(r)
            rid, out = r.id, summary_json(r)
        _running_id = rid
    except BaseException:
        _run_lock.release()
        raise
    hub.broadcast({"type": "nightly", "action": "started", "report": out})
    threading.Thread(target=_run, args=(rid, mode, trigger, apply_from, idx), name="vg-nightly",
                     daemon=True).start()
    return out


def _run(rid: int, mode: str, trigger: str, apply_from: Optional[int], idx: Optional[list[int]]) -> None:
    global _running_id
    t0 = _time.monotonic()
    try:
        try:
            if apply_from is not None:
                actions = _apply_preview(rid, apply_from, idx)
            elif mode == "apply":
                actions = _run_apply(rid)
            else:
                actions = _plan(settings_store.get())
            summary = _summary(actions)
            text = _text(mode, summary, actions)
            with SessionLocal() as s:
                r = s.get(NightlyReport, rid)
                r.status, r.finished_at, r.summary, r.actions, r.text = "done", utcnow(), summary, actions, text
                s.commit()
                out = summary_json(r)
            log.info("Nachtlauf %s (%s/%s) fertig in %.1f s: %s", rid, mode, trigger, _time.monotonic() - t0, text)
            if (trigger != "manual" or mode == "apply") and actions:   # „nichts zu tun“ nicht jede Nacht melden
                _notify(rid, text)
        except Exception as e:  # noqa: BLE001 – Bericht als fehlgeschlagen speichern, Backend läuft weiter
            log.exception("Nachtlauf %s fehlgeschlagen", rid)
            with SessionLocal() as s:
                r = s.get(NightlyReport, rid)
                # Eine angewandte Vorschau bleibt gesperrt (Teile können schon angewandt sein) – ein neuer Probelauf
                # zeigt den aktuellen Stand.
                r.status, r.finished_at, r.error = "error", utcnow(), f"{type(e).__name__}: {e}"[:500]
                s.commit()
                out = summary_json(r)
        hub.broadcast({"type": "nightly", "action": "finished", "report": out})
    finally:
        _running_id = None
        _run_lock.release()


def _notify(rid: int, text: str) -> None:
    try:
        from . import notifications
        notifications.create(source="report", title="Nachtlauf (Gesichter)", message=text, level="info",
                             data={"nightly_report_id": rid}, targets=[], push_enabled=False,
                             click_path="/einstellungen#s-nachtlauf")
    except Exception:  # noqa: BLE001
        log.exception("Hinweis zum Nachtlauf fehlgeschlagen")


# ---------------------------------------------------------------- Daten laden


class _Face:
    __slots__ = ("ident", "samples", "vecs", "sample_ids", "events", "n_samples")

    def __init__(self, ident: Identity):
        self.ident = ident
        self.samples: list[IdentitySample] = []   # nicht negative
        self.vecs: Optional[np.ndarray] = None    # normierte Embeddings (Hauptdimension)
        self.sample_ids: list[int] = []           # Sample-IDs zu den Zeilen von vecs
        self.events = 0
        self.n_samples = 0                        # alle Bilder (auch negative)


def _load(s: Session) -> dict[int, _Face]:
    faces = {i.id: _Face(i) for i in s.scalars(select(Identity).where(Identity.kind == "face")).all()}
    if not faces:
        return faces
    samples = s.scalars(select(IdentitySample).where(IdentitySample.identity_id.in_(list(faces)))
                        .order_by(IdentitySample.id)).all()
    dims: dict[int, int] = {}
    for x in samples:
        faces[x.identity_id].n_samples += 1
        if x.negative:
            continue
        faces[x.identity_id].samples.append(x)
        if x.embedding is not None and len(x.embedding):
            dims[len(x.embedding)] = dims.get(len(x.embedding), 0) + 1
    d = max(dims, key=dims.get) if dims else -1
    for f in faces.values():
        rows = [(x.id, x.embedding) for x in f.samples if x.embedding is not None and len(x.embedding) == d]
        if rows:
            f.sample_ids = [i for i, _ in rows]
            f.vecs = face_match._norm(np.asarray([e for _, e in rows], dtype=np.float32))
    for iid, n in s.execute(select(Event.identity_id, func.count()).where(Event.identity_id.in_(list(faces)))
                            .group_by(Event.identity_id)).all():
        faces[iid].events = int(n)
    return faces


def _named(f: _Face) -> bool:
    return bool(f.ident.name)


def _fixed(f: _Face) -> bool:
    """Feste Identität: benannt oder mit Lernsperre – nie Quelle eines automatischen Merges."""
    return _named(f) or bool(f.ident.learning_locked)


def _score_vs(faces: dict[int, _Face], src: _Face, target_ids: list[int]) -> float:
    """Score der Quelle gegen die Vereinigung der Bilder von `target_ids` (ID.F, Top-3-Mittel)."""
    q = face_match.mean_embedding(list(src.vecs)) if src.vecs is not None else None
    mats = [faces[t].vecs for t in target_ids if t in faces and faces[t].vecs is not None]
    if q is None or not mats:
        return 0.0
    mat = np.concatenate(mats)
    sc = face_match.top_k_scores(mat, np.zeros(mat.shape[0], dtype=np.int64), q)
    return float(sc.get(0, 0.0))


def _blocked(s: Session) -> set[tuple[int, int]]:
    """„Nein“-Paare in beiden Richtungen (ein Nein gilt für das Paar, nicht für die Richtung)."""
    out = set()
    for a, b in ident_svc._blocked_pairs(s):
        out.add((a, b))
        out.add((b, a))
    return out


def _ident_ref(f: _Face, covers: dict[int, str], prefix: str) -> dict[str, Any]:
    return {f"{prefix}_id": f.ident.id, f"{prefix}_display_name": f.ident.display_name,
            f"{prefix}_cover_url": media_url(covers.get(f.ident.id))}


# ---------------------------------------------------------------- Planen (ändert nichts)


def _plan_merges(s: Session, faces: dict[int, _Face], st: dict[str, Any],
                 covers: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Schritt 1: (Merge-Aktionen, Rückfrage-Aktionen) ohne idx."""
    from . import review
    auto_min = float(st.get("nightly_auto_merge_min", 0.55))
    margin = float(st.get("nightly_auto_merge_margin", 0.08))
    review_min = float(st.get("nightly_review_min", 0.40))
    max_reviews = int(st.get("nightly_review_max_per_day", 5))
    blocked = _blocked(s)

    with_vecs = [f for f in faces.values() if f.vecs is not None]
    if len(with_vecs) < 2:
        return [], []
    mat = np.concatenate([f.vecs for f in with_vecs])
    ids = np.concatenate([np.full(f.vecs.shape[0], f.ident.id, dtype=np.int64) for f in with_vecs])

    same_cache: dict[tuple[int, int], bool] = {}

    def same(a: int, b: int) -> bool:
        """Ähneln sich zwei Kandidaten selbst sicher (in einer der beiden Richtungen)?"""
        key = (min(a, b), max(a, b))
        if key not in same_cache:
            fa, fb = faces[a], faces[b]
            same_cache[key] = (fa.vecs is not None and fb.vecs is not None
                               and max(_score_vs(faces, fa, [b]), _score_vs(faces, fb, [a])) >= auto_min)
        return same_cache[key]

    best: dict[int, tuple[int, float, Optional[float]]] = {}   # src → (Kandidat, Score, Zweitbester)
    for f in with_vecs:
        if _fixed(f):
            continue
        q = face_match.mean_embedding(list(f.vecs))
        if q is None:
            continue
        mask = ids != f.ident.id
        sc = face_match.top_k_scores(mat[mask], ids[mask], q)
        ranked = sorted(((v, k) for k, v in sc.items() if (f.ident.id, k) not in blocked), reverse=True)
        if not ranked:
            continue
        # Zweitbester = bester *konkurrierender* Kandidat. Weitere Fragmente derselben Person (untereinander
        # selbst ≥ auto_min ähnlich) sind keine Konkurrenz – sonst verhinderten sie sich gegenseitig.
        top = ranked[0][1]
        second = next((v for v, k in ranked[1:] if not same(top, k)), None)
        best[f.ident.id] = (top, ranked[0][0], second)

    # Sichere Kanten → Bündel (Union-Find)
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    safe = {sid: v for sid, v in best.items()
            if v[1] >= auto_min and (v[2] is None or v[1] - v[2] >= margin)}
    for sid, (tid, _, _) in safe.items():
        parent[find(sid)] = find(tid)
    clusters: dict[int, list[int]] = {}
    for x in list(parent):
        clusters.setdefault(find(x), []).append(x)

    def rank_target(i: int) -> tuple:
        f = faces[i]
        return (_named(f), bool(f.ident.learning_locked), f.events, len(f.samples), -i)

    merges: list[dict[str, Any]] = []
    review_cands: dict[int, tuple[int, float, Optional[float]]] = {}
    for members in clusters.values():
        fixed = [m for m in members if _fixed(faces[m])]
        if len(fixed) > 1:     # zwei Benannte/Gesperrte im Bündel → nie automatisch, stattdessen Rückfragen
            for m in members:
                if m in safe:
                    review_cands[m] = safe[m]
            continue
        target = max(members, key=rank_target)
        tf = faces[target]
        for m in sorted(members):
            if m == target:
                continue
            sf = faces[m]
            direct = _score_vs(faces, sf, [target])
            if direct < review_min:   # Ketten-Drift: Quelle ähnelt dem Ziel selbst kaum → nur nachfragen
                review_cands[m] = (target, direct, None)
                continue
            if (m, target) in blocked:
                continue
            _, sc, second = safe.get(m, best.get(m, (target, direct, None)))
            merges.append({
                "type": "merge", **_ident_ref(sf, covers, "src"), **_ident_ref(tf, covers, "into"),
                "into_named": _named(tf), "score": round(float(sc), 4),
                "direct_score": round(float(direct), 4),
                "second_score": None if second is None else round(float(second), 4),
                "samples": len(sf.samples), "events": sf.events, "review_id": None, "merge_id": None,
            })

    merged_src = {a["src_id"] for a in merges}
    in_bundle = {m for a in merges for m in (a["src_id"], a["into_id"])}
    for sid, (tid, sc, second) in best.items():
        if sid in review_cands or sc < review_min or (sid in in_bundle and tid in in_bundle):
            continue
        review_cands[sid] = (tid, sc, second)
    reviews: list[dict[str, Any]] = []
    asked: set[frozenset] = set()
    for sid, (tid, sc, second) in sorted(review_cands.items(), key=lambda kv: kv[1][1], reverse=True):
        if len(reviews) >= max_reviews:
            break
        if sid in merged_src or tid in merged_src or tid not in faces or (_named(faces[sid]) and _named(faces[tid])):
            continue
        pair = frozenset((sid, tid))
        if pair in asked or (sid, tid) in blocked or review.is_blocked(s, f"idm:{sid}:{tid}") \
                or review.is_blocked(s, f"idm:{tid}:{sid}"):
            continue
        asked.add(pair)
        sf, tf = faces[sid], faces[tid]
        reviews.append({
            "type": "review", **_ident_ref(sf, covers, "src"), **_ident_ref(tf, covers, "into"),
            "into_named": _named(tf), "score": round(float(sc), 4),
            "second_score": None if second is None else round(float(second), 4),
            "samples": len(sf.samples), "events": sf.events, "review_id": None, "merge_id": None,
        })
    return merges, reviews


def _plan_curate(f: _Face, st: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Schritt 2 für eine Identität: Ausreißer → negativ, Vielfalt bis sample_limit (nur automatische entfernen)."""
    if f.ident.learning_locked or len(f.samples) < CURATE_MIN_SAMPLES or f.vecs is None \
            or f.vecs.shape[0] < CURATE_MIN_SAMPLES:
        return None
    by_id = {x.id: x for x in f.samples}
    centroid = face_match._norm(f.vecs.mean(axis=0))
    sims = f.vecs @ centroid
    med = float(np.median(sims))
    out = sorted(((float(v), sid) for sid, v in zip(f.sample_ids, sims)
                  if float(v) < med - OUTLIER_DROP and not by_id[sid].manual))
    negatives = [sid for _, sid in out[:int(len(f.samples) * MAX_NEGATIVE_SHARE)]]
    remaining = [x for x in f.samples if x.id not in set(negatives)]
    limit = face_match.sample_limit(f.ident, st)
    removed: list[int] = []
    if len(remaining) > limit:
        keep = set(face_match.select_gallery(remaining, limit))
        removed = [x.id for x in remaining if x.id not in keep and not x.manual]
    if not negatives and not removed:
        return None
    return {"type": "curate", "identity_id": f.ident.id, "display_name": f.ident.display_name,
            "negative_sample_ids": negatives, "removed_sample_ids": removed}


def _person_threshold(f: _Face) -> Optional[float]:
    """Schritt 3: Schwelle aus der Streuung – Perzentil der Leave-one-out-Scores (Top-3) minus Puffer."""
    if f.vecs is None or f.vecs.shape[0] < THRESHOLD_MIN_SAMPLES:
        return None
    sims = f.vecs @ f.vecs.T
    np.fill_diagonal(sims, -np.inf)
    k = min(face_match.TOP_K, sims.shape[0] - 1)
    loo = np.sort(sims, axis=1)[:, -k:].mean(axis=1)
    thr = float(np.percentile(loo, THRESHOLD_PCT)) - THRESHOLD_SLACK
    return round(min(max(thr, THRESHOLD_RANGE[0]), THRESHOLD_RANGE[1]), 2)


def _plan_rest(s: Session, faces: dict[int, _Face], st: dict[str, Any], skip: set[int],
               covers: dict[int, str]) -> list[dict[str, Any]]:
    """Schritte 2–4 für alle Identitäten außer `skip` (Quellen geplanter Merges)."""
    acts: list[dict[str, Any]] = []
    glob = float(st.get("face_match_threshold", 0.30))
    for f in sorted(faces.values(), key=lambda x: x.ident.id):
        if f.ident.id in skip:
            continue
        if st.get("nightly_curate", True):
            a = _plan_curate(f, st)
            if a:
                acts.append({**a, "cover_url": media_url(covers.get(f.ident.id))})
        if st.get("nightly_thresholds", True) and _named(f):
            new = _person_threshold(f)
            old = f.ident.match_threshold
            if new is not None and abs(new - (old if old is not None else glob)) >= 0.01:
                acts.append({"type": "threshold", "identity_id": f.ident.id, "display_name": f.ident.display_name,
                             "cover_url": media_url(covers.get(f.ident.id)), "old": old, "new": new})
    days = int(st.get("nightly_cleanup_days", 3))
    if days > 0:
        cutoff = utcnow() - timedelta(days=days)
        for f in sorted(faces.values(), key=lambda x: x.ident.id):
            if f.ident.id in skip or not _cleanable(f.ident, f.n_samples, f.events, cutoff):
                continue
            acts.append({"type": "cleanup", "identity_id": f.ident.id, "display_name": f.ident.display_name,
                         "cover_url": media_url(covers.get(f.ident.id)), "samples": f.n_samples})
    return acts


def _cleanable(ident: Identity, n_samples: int, events: int, cutoff: datetime) -> bool:
    return (not ident.name and not ident.learning_locked and n_samples <= 1 and events == 0
            and ident.last_seen is not None and ident.last_seen < cutoff)


def _plan(st: dict[str, Any]) -> list[dict[str, Any]]:
    """Probelauf: alle Schritte berechnen, nichts ändern."""
    with SessionLocal() as s:
        faces = _load(s)
        covers = ident_svc._covers(s, list(faces))
        merges, reviews = _plan_merges(s, faces, st, covers)
        rest = _plan_rest(s, faces, st, {a["src_id"] for a in merges}, covers)
        s.rollback()   # nur gelesen
    return _number(merges + reviews + rest)


def _number(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for i, a in enumerate(actions):
        a["idx"] = i
        a.setdefault("applied", False)
        a.setdefault("skipped_reason", None)
    return actions


# ---------------------------------------------------------------- Anwenden


def _run_apply(rid: int) -> list[dict[str, Any]]:
    """Geplanter/sofortiger Lauf im Modus apply: Schritt 1 anwenden, danach Schritte 2–4 auf dem neuen Stand."""
    st = settings_store.get()
    with SessionLocal() as s:
        faces = _load(s)
        covers = ident_svc._covers(s, list(faces))
        merges, reviews = _plan_merges(s, faces, st, covers)
        s.rollback()
    actions = _number(merges + reviews)
    _apply_actions(rid, actions, st)
    with SessionLocal() as s:
        faces = _load(s)
        covers = ident_svc._covers(s, list(faces))
        rest = _plan_rest(s, faces, st, set(), covers)
        s.rollback()
    rest = [{**a, "idx": len(actions) + i, "applied": False, "skipped_reason": None} for i, a in enumerate(rest)]
    _apply_actions(rid, rest, st)
    return actions + rest


def _apply_preview(rid: int, src_id: int, idx: Optional[list[int]]) -> list[dict[str, Any]]:
    with SessionLocal() as s:
        src = s.get(NightlyReport, src_id)
        planned = [dict(a) for a in (src.actions or [])]
    if idx is not None:
        want = {int(i) for i in idx}
        planned = [a for a in planned if int(a.get("idx", -1)) in want]
    for a in planned:
        a["applied"], a["skipped_reason"] = False, None
    _apply_actions(rid, planned, settings_store.get())
    return planned


def _apply_actions(rid: int, actions: list[dict[str, Any]], st: dict[str, Any]) -> None:
    """Wendet Aktionen an; jede wird neu geprüft (sonst `skipped_reason`). Ändert `actions` in place."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for a in actions:
        by_type.setdefault(a.get("type", ""), []).append(a)
    _apply_merges(rid, by_type.get("merge", []), st)
    _apply_reviews(by_type.get("review", []))
    _apply_curate(by_type.get("curate", []), st)
    _apply_thresholds(by_type.get("threshold", []))
    _apply_cleanup(by_type.get("cleanup", []), st)


def _apply_merges(rid: int, acts: list[dict[str, Any]], st: dict[str, Any]) -> None:
    from .identity_bulk import BulkError, bulk_merge
    if not acts:
        return
    auto_min = float(st.get("nightly_auto_merge_min", 0.55))
    review_min = float(st.get("nightly_review_min", 0.40))
    groups: dict[int, list[dict[str, Any]]] = {}
    for a in acts:
        groups.setdefault(int(a["into_id"]), []).append(a)
    with SessionLocal() as s:   # Bündel sind disjunkt (jede Quelle in genau einem, Ziele nie Quelle) → einmal laden
        faces = _load(s)
        blocked = _blocked(s)
        s.expunge_all()   # nur gelesen; Objekte losgelöst weiter nutzen (rollback würde sie verfallen lassen)
    for into_id, group in groups.items():
        dst = faces.get(into_id)
        ok: list[dict[str, Any]] = []
        for a in group:
            sid = int(a["src_id"])
            sf = faces.get(sid)
            if dst is None:
                a["skipped_reason"] = "Ziel existiert nicht mehr"
            elif sf is None:
                a["skipped_reason"] = "Quelle existiert nicht mehr"
            elif sf.ident.name:
                a["skipped_reason"] = "Quelle inzwischen benannt"
            elif sf.ident.learning_locked:
                a["skipped_reason"] = "Quelle inzwischen gesperrt"
            elif (sid, into_id) in blocked:
                a["skipped_reason"] = "Paar wurde mit „Nein“ beantwortet"
            elif sf.vecs is None:
                a["skipped_reason"] = "Quelle ohne Gesichtsmerkmale"
            else:
                ok.append(a)
        # Score neu: gegen Ziel + übrige Quellen des Bündels (die mit ihm zusammengeführt werden) …
        members = [into_id] + [int(a["src_id"]) for a in ok]
        passing: list[dict[str, Any]] = []
        for a in ok:
            sid = int(a["src_id"])
            union = _score_vs(faces, faces[sid], [m for m in members if m != sid])
            direct = _score_vs(faces, faces[sid], [into_id])
            if union < auto_min or direct < review_min:   # … und direkt zum Ziel nicht zu weit weg (wie geplant)
                a["skipped_reason"] = f"Ähnlichkeit jetzt {union:.2f} (unter {auto_min:.2f})" if union < auto_min \
                    else f"Ähnlichkeit zum Ziel jetzt {direct:.2f}"
                continue
            a["score"] = round(union, 4)
            passing.append(a)
        if not passing:
            continue
        with SessionLocal() as s:
            try:
                _, srcs, merge_ids = bulk_merge(s, [int(a["src_id"]) for a in passing], into_id, actor="nightly",
                                                score={int(a["src_id"]): float(a["score"]) for a in passing},
                                                report_id=rid)
            except BulkError as e:
                for a in passing:
                    a["skipped_reason"] = e.detail
                continue
            except Exception as e:  # noqa: BLE001 – ein Bündel darf die übrigen nicht verhindern
                log.exception("Nachtlauf: Zusammenführen in %s fehlgeschlagen", into_id)
                for a in passing:
                    a["skipped_reason"] = f"Fehler: {e}"[:200]
                continue
        mids = dict(zip(srcs, merge_ids))
        for a in passing:
            a["applied"], a["merge_id"] = True, mids.get(int(a["src_id"]))


def _apply_reviews(acts: list[dict[str, Any]]) -> None:
    from . import review
    for a in acts:
        with SessionLocal() as s:
            src, dst = s.get(Identity, int(a["src_id"])), s.get(Identity, int(a["into_id"]))
            covers = ident_svc._covers(s, [int(a["src_id"]), int(a["into_id"])])
        if src is None or dst is None:
            a["skipped_reason"] = "Identität existiert nicht mehr"
            continue
        if src.name and dst.name:
            a["skipped_reason"] = "Beide inzwischen benannt"
            continue
        try:
            r = review.create(
                kind="identity_match", dedup_key=f"idm:{src.id}:{dst.id}",
                question=f"Ist „{src.display_name}“ dieselbe Person wie „{dst.display_name}“?",
                priority=float(a.get("score") or 0), identity_id=dst.id, source_identity_id=src.id,
                score=round(float(a.get("score") or 0), 4), crop_path=covers.get(src.id),
                snapshot_path=covers.get(dst.id),
                data={"identity_kind": "face", "target_name": dst.display_name, "source_name": src.display_name,
                      "reason": "nightly"})
        except Exception as e:  # noqa: BLE001
            log.exception("Nachtlauf: Rückfrage fehlgeschlagen")
            a["skipped_reason"] = f"Fehler: {e}"[:200]
            continue
        if r is None:
            a["skipped_reason"] = "Rückfragen aus, Limit erreicht oder Paar schon gefragt"
        else:
            a["applied"], a["review_id"] = True, r["id"]


def _apply_curate(acts: list[dict[str, Any]], st: dict[str, Any]) -> None:
    from .serializers import identity_json
    for a in acts:
        iid = int(a["identity_id"])
        to_remove: list[str] = []
        with ident_svc._lock, SessionLocal() as s:
            try:
                ident = s.scalars(select(Identity).where(Identity.id == iid).with_for_update()).first()
                if ident is None:
                    a["skipped_reason"] = "Identität existiert nicht mehr"
                    continue
                if ident.learning_locked:
                    a["skipped_reason"] = "Lernsperre aktiv"
                    continue
                own = {x.id: x for x in s.scalars(select(IdentitySample).where(
                    IdentitySample.identity_id == iid)).all()}
                neg = [own[i] for i in a.get("negative_sample_ids") or []
                       if i in own and not own[i].manual and not own[i].negative]
                rem = [own[i] for i in a.get("removed_sample_ids") or []
                       if i in own and not own[i].manual and not own[i].negative]
                if not neg and not rem:
                    a["skipped_reason"] = "Bilder inzwischen geändert"
                    continue
                for x in neg:
                    x.negative, x.source = True, "nightly"
                for x in rem:
                    if x.image_path:
                        to_remove.append(x.image_path)
                    s.delete(x)
                s.commit()
                a["negative_sample_ids"] = [x.id for x in neg]
                a["removed_sample_ids"] = [x.id for x in rem]
                a["applied"] = True
                out = identity_json(s, ident)
            except BaseException:
                s.rollback()
                raise
        for p in to_remove:
            ident_svc._remove_media(p)
        ident_svc.invalidate("face")
        hub.broadcast({"type": "identity", "identity": out})


def _apply_thresholds(acts: list[dict[str, Any]]) -> None:
    from .serializers import identity_json
    for a in acts:
        with ident_svc._lock, SessionLocal() as s:
            ident = s.get(Identity, int(a["identity_id"]))
            if ident is None or ident.kind != "face":
                a["skipped_reason"] = "Identität existiert nicht mehr"
                continue
            if not ident.name:
                a["skipped_reason"] = "Nicht mehr benannt"
                continue
            new = min(max(float(a["new"]), THRESHOLD_RANGE[0]), THRESHOLD_RANGE[1])
            a["old"] = ident.match_threshold
            ident.match_threshold = new
            s.commit()
            a["applied"] = True
            out = identity_json(s, ident)
        hub.broadcast({"type": "identity", "identity": out})


def _apply_cleanup(acts: list[dict[str, Any]], st: dict[str, Any]) -> None:
    from .identity_bulk import BulkError, bulk_delete
    if not acts:
        return
    days = max(1, int(st.get("nightly_cleanup_days", 3) or 1))
    cutoff = utcnow() - timedelta(days=days)
    ok: list[dict[str, Any]] = []
    with SessionLocal() as s:
        for a in acts:
            iid = int(a["identity_id"])
            ident = s.get(Identity, iid)
            if ident is None or ident.kind != "face":
                a["skipped_reason"] = "Identität existiert nicht mehr"
                continue
            n = s.scalar(select(func.count()).select_from(IdentitySample).where(
                IdentitySample.identity_id == iid)) or 0
            ev = s.scalar(select(func.count()).select_from(Event).where(Event.identity_id == iid)) or 0
            if not _cleanable(ident, int(n), int(ev), cutoff):
                a["skipped_reason"] = "Inzwischen benannt, gesperrt, gesehen oder mit Ereignissen"
                continue
            ok.append(a)
        s.rollback()
    if not ok:
        return
    with SessionLocal() as s:
        try:
            bulk_delete(s, [int(a["identity_id"]) for a in ok])
        except BulkError as e:
            for a in ok:
                a["skipped_reason"] = e.detail
            return
    for a in ok:
        a["applied"] = True


# ---------------------------------------------------------------- Bericht


def _summary(actions: list[dict[str, Any]]) -> dict[str, Any]:
    with SessionLocal() as s:
        total = s.scalar(select(func.count()).select_from(Identity).where(Identity.kind == "face")) or 0
        frags = s.scalar(select(func.count()).select_from(Identity).where(
            Identity.kind == "face", (Identity.name.is_(None)) | (Identity.name == ""))) or 0
    live = [a for a in actions if not a.get("skipped_reason")]
    cur = [a for a in live if a["type"] == "curate"]
    return {"identities": int(total), "fragments": int(frags),
            "merges": sum(1 for a in live if a["type"] == "merge"),
            "reviews": sum(1 for a in live if a["type"] == "review"),
            "curated": len(cur),
            "negatives": sum(len(a.get("negative_sample_ids") or []) for a in cur),
            "removed_samples": sum(len(a.get("removed_sample_ids") or []) for a in cur),
            "thresholds": sum(1 for a in live if a["type"] == "threshold"),
            "cleanup": sum(1 for a in live if a["type"] == "cleanup"),
            "skipped": sum(1 for a in actions if a.get("skipped_reason"))}


def _text(mode: str, sm: dict[str, Any], actions: list[dict[str, Any]]) -> str:
    preview = mode == "preview"
    parts: list[str] = []
    if sm["merges"]:
        targets = {a["into_display_name"] for a in actions
                   if a["type"] == "merge" and not a.get("skipped_reason") and a.get("into_named")}
        n = sm["merges"]
        t = f"{n} Fragment{'e' if n != 1 else ''} " + \
            (("würden" if n != 1 else "würde") + " zusammengeführt" if preview else "zusammengeführt")
        if targets:
            t += f" (u. a. zu {', '.join(sorted(targets)[:3])})"
        parts.append(t)
    if sm["reviews"]:
        n = sm["reviews"]
        parts.append(f"{n} Rückfrage{'n' if n != 1 else ''}"
                     + ((" würden" if n != 1 else " würde") + " gestellt" if preview else ""))
    if sm["curated"]:
        names = sorted({a["display_name"] for a in actions if a["type"] == "curate" and not a.get("skipped_reason")})
        parts.append(f"Galerie von {', '.join(names[:3])}{' …' if len(names) > 3 else ''} "
                     + ("würde aktualisiert" if preview else "aktualisiert"))
    if sm["thresholds"]:
        n = sm["thresholds"]
        parts.append(f"{n} Schwelle{'n' if n != 1 else ''} je Person "
                     + (("würden" if n != 1 else "würde") + " angepasst" if preview else "angepasst"))
    if sm["cleanup"]:
        n = sm["cleanup"]
        parts.append(f"{n} alte{'s' if n == 1 else ''} Einzelbild-Fragment{'e' if n != 1 else ''} "
                     + (("würden" if n != 1 else "würde") + " gelöscht" if preview else "gelöscht"))
    if not parts:
        parts.append("nichts zu tun")
    if sm.get("skipped"):
        parts.append(f"{sm['skipped']} übersprungen")
    return ("Probelauf: " if preview else "Nachtlauf: ") + ", ".join(parts) + "."
