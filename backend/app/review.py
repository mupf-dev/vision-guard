"""Rückfragen an den Nutzer („Bitte bestätigen“) – gemeinsamer Kern (W2, K2).

Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.D/W2.I. Hier fertig: Anlegen mit Dedup und Limits, Beantworten
(mit Wirkung je Art über `register_kind`), Verfall, JSON, WS `review`. Erzeuger (Inventar `uncertain`, Exemplar-
Treffer, Gesichter-/Tier-Vorschläge), Antwort-Wirkungen, Schwellen-Nachführung, Push und signierte Antwort-Links
baut D2 (bzw. weitere Themen über eigene Arten, z. B. C3 „Welches Tier?“).

    from . import review
    review.register_kind("inventory_label", label_de="Gegenstand richtig erkannt?", on_answer=fn)
    review.create(kind="inventory_label", dedup_key="lbl:3:remote:45", question="Ist das eine Fernbedienung?",
                  camera_id=3, label="remote", score=0.31, threshold=0.35, crop_path="inventory/…jpg", priority=0.9)
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import registry, settings_store
from .config import config
from .db import SessionLocal
from .models import ReviewRequest, utcnow
from .schemas import iso, media_url
from .ws import hub

log = logging.getLogger("vg.review")

KINDS_BUILTIN = ("inventory_label", "inventory_identity", "identity_match")
ANSWERS = ("yes", "no", "skip")
SOURCES = ("ui", "push", "mcp", "api")
YES_BLOCK_DAYS = 30          # mit „Ja“ beantwortet → 30 Tage nicht erneut fragen; „Nein“ → nie wieder
TICK_EVERY_S = 60.0

AnswerHandler = Callable[[Session, ReviewRequest, str, Optional[str]], None]
CreatedCallback = Callable[[dict[str, Any]], None]


class ReviewError(Exception):
    """Fachlicher Fehler beim Beantworten (Router: 404/409/422 je `status`)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class KindSpec:
    kind: str
    label_de: str
    on_answer: Optional[AnswerHandler]


KINDS: dict[str, KindSpec] = {}
_created_cbs: list[CreatedCallback] = []
_lock = threading.Lock()
_last_tick = 0.0


def register_kind(kind: str, *, label_de: str = "", on_answer: Optional[AnswerHandler] = None) -> KindSpec:
    """Art registrieren bzw. Antwort-Wirkung nachreichen. `on_answer(session, req, answer, correct_label)` läuft
    bei „yes“/„no“ vor dem Commit (gleiche Session); eine Ausnahme verwirft die Antwort (422)."""
    prev = KINDS.get(kind)
    spec = KindSpec(kind=kind, label_de=label_de or (prev.label_de if prev else kind),
                    on_answer=on_answer or (prev.on_answer if prev else None))
    KINDS[kind] = spec
    return spec


def on_created(callback: CreatedCallback) -> None:
    """Callback nach dem Anlegen einer Frage (D2: Push über notify_routes.review, MQTT `{prefix}/review`).
    Läuft im Aufrufer-Thread nach dem Commit – kurz halten."""
    _created_cbs.append(callback)


for _k, _l in (("inventory_label", "Gegenstand richtig erkannt?"),
               ("inventory_identity", "Ist das dieses Exemplar?"),
               ("identity_match", "Dieselbe Person bzw. dasselbe Tier?")):
    register_kind(_k, label_de=_l)


# ------------------------------------------------------------------ JSON


def review_json(r: ReviewRequest, signed: bool = False) -> dict[str, Any]:
    from .inventory_names import display_name, vocab_map

    cam = registry.get(r.camera_id) if r.camera_id is not None else None
    name_de = None
    if r.label:
        name_de = display_name(r.label, vocab_map(settings_store.get().get("inventory_vocabulary") or []))
    data = {
        "id": r.id, "kind": r.kind, "status": r.status, "question": r.question,
        "camera_id": r.camera_id, "camera_name": cam["name"] if cam else None,
        "label": r.label, "name_de": name_de,
        "inventory_item_id": r.inventory_item_id, "identity_id": r.identity_id,
        "source_identity_id": r.source_identity_id,
        "score": round(float(r.score), 4) if r.score is not None else None,
        "threshold": round(float(r.threshold), 4) if r.threshold is not None else None,
        "bbox": list(r.bbox) if r.bbox else None,
        "crop_url": media_url(r.crop_path), "snapshot_url": media_url(r.snapshot_path),
        "data": dict(r.data or {}),
        "created_at": iso(r.created_at), "answered_at": iso(r.answered_at), "answer_source": r.answer_source,
        "expires_at": iso(r.expires_at), "correct_label": r.correct_label,
    }
    if signed:
        from .media_sign import sign_urls
        data = sign_urls(data)
    return data


def _broadcast(action: str, r: ReviewRequest) -> None:
    hub.broadcast({"type": "review", "action": action, "review": review_json(r)})


# ------------------------------------------------------------------ Zählen / Limits


def _local_midnight_utc(now: Optional[datetime] = None) -> datetime:
    loc = (now or utcnow()).astimezone(config.tz)
    return loc.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def counts(s: Session) -> dict[str, int]:
    st = settings_store.get()
    open_count = s.scalar(select(func.count()).select_from(ReviewRequest)
                          .where(ReviewRequest.status == "open")) or 0
    asked_today = s.scalar(select(func.count()).select_from(ReviewRequest)
                           .where(ReviewRequest.created_at >= _local_midnight_utc())) or 0
    return {"open_count": int(open_count), "asked_today": int(asked_today),
            "max_per_day": int(st.get("review_max_per_day", 10))}


def is_blocked(s: Session, dedup_key: str, now: Optional[datetime] = None) -> bool:
    """Dedup: offen, in den letzten 30 Tagen mit „Ja“ oder jemals mit „Nein“ beantwortet."""
    now = now or utcnow()
    rows = s.execute(select(ReviewRequest.status, ReviewRequest.answered_at)
                     .where(ReviewRequest.dedup_key == dedup_key,
                            ReviewRequest.status.in_(("open", "yes", "no")))).all()
    for status, answered in rows:
        if status in ("open", "no"):
            return True
        if status == "yes" and answered is not None and answered >= now - timedelta(days=YES_BLOCK_DAYS):
            return True
    return False


# ------------------------------------------------------------------ Anlegen


def create(*, kind: str, dedup_key: str, question: str, priority: float = 0.0,
           camera_id: Optional[int] = None, label: Optional[str] = None, inventory_item_id: Optional[int] = None,
           identity_id: Optional[int] = None, source_identity_id: Optional[int] = None,
           score: Optional[float] = None, threshold: Optional[float] = None, bbox: Optional[list[float]] = None,
           crop_path: Optional[str] = None, snapshot_path: Optional[str] = None,
           data: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Neue Frage anlegen. None, wenn Rückfragen aus sind, der Schlüssel gesperrt ist (Dedup) oder ein Limit
    (`review_max_per_day`, `review_max_open`) erreicht ist. Thread-sicher; eigene DB-Session."""
    st = settings_store.get()
    if not st.get("review_enabled", True):
        return None
    if kind not in KINDS:
        raise ValueError(f"unbekannte Rückfrage-Art: {kind}")
    dedup_key = str(dedup_key)[:200]
    with _lock, SessionLocal() as s:
        now = utcnow()
        if is_blocked(s, dedup_key, now):
            return None
        c = counts(s)
        if c["asked_today"] >= int(st.get("review_max_per_day", 10)):
            log.debug("Rückfrage %s verworfen: Tageslimit erreicht", dedup_key)
            return None
        if c["open_count"] >= int(st.get("review_max_open", 20)):
            log.debug("Rückfrage %s verworfen: zu viele offene Fragen", dedup_key)
            return None
        r = ReviewRequest(kind=kind, status="open", dedup_key=dedup_key, priority=float(priority or 0),
                          question=question[:1000], camera_id=camera_id, label=label,
                          inventory_item_id=inventory_item_id, identity_id=identity_id,
                          source_identity_id=source_identity_id, score=score, threshold=threshold,
                          bbox=[round(float(x), 4) for x in bbox] if bbox else None, crop_path=crop_path,
                          snapshot_path=snapshot_path, data=dict(data or {}), created_at=now,
                          expires_at=now + timedelta(hours=int(st.get("review_expire_h", 48))))
        s.add(r)
        s.commit()
        s.refresh(r)
        out = review_json(r)
    hub.broadcast({"type": "review", "action": "new", "review": out})
    for cb in list(_created_cbs):
        try:
            cb(out)
        except Exception:
            log.exception("Rückfrage-Callback fehlgeschlagen")
    log.info("Rückfrage %s angelegt (%s): %s", out["id"], kind, question)
    return out


# ------------------------------------------------------------------ Beantworten


def answer(s: Session, review_id: int, answer: str, *, source: str = "ui",
           correct_label: Optional[str] = None) -> dict[str, Any]:
    if answer not in ANSWERS:
        raise ReviewError(422, "Antwort muss yes, no oder skip sein")
    if source not in SOURCES:
        source = "api"
    r = s.get(ReviewRequest, review_id)
    if r is None:
        raise ReviewError(404, "Rückfrage nicht gefunden")
    if r.status != "open":
        raise ReviewError(409, "Frage ist nicht mehr offen")
    if r.expires_at is not None and r.expires_at <= utcnow():
        r.status = "expired"
        s.commit()
        _broadcast("expired", r)
        raise ReviewError(409, "Frage ist abgelaufen")
    r.status = {"yes": "yes", "no": "no", "skip": "skipped"}[answer]
    r.answered_at = utcnow()
    r.answer_source = source
    r.correct_label = correct_label if answer == "no" else None
    spec = KINDS.get(r.kind)
    if answer in ("yes", "no") and spec and spec.on_answer:
        try:
            spec.on_answer(s, r, answer, r.correct_label)
        except ReviewError:
            s.rollback()
            raise
        except Exception as e:
            s.rollback()
            log.exception("Rückfrage %s: Antwort-Wirkung fehlgeschlagen", review_id)
            raise ReviewError(422, f"Antwort konnte nicht angewendet werden: {e}") from e
    s.commit()
    s.refresh(r)
    out = review_json(r)
    hub.broadcast({"type": "review", "action": "answered", "review": out})
    log.info("Rückfrage %s beantwortet: %s (%s)", review_id, answer, source)
    return out


# ------------------------------------------------------------------ Verfall


def expire_due() -> int:
    with SessionLocal() as s:
        rows = s.scalars(select(ReviewRequest).where(ReviewRequest.status == "open",
                                                     ReviewRequest.expires_at <= utcnow())).all()
        if not rows:
            return 0
        s.execute(update(ReviewRequest).where(ReviewRequest.id.in_([r.id for r in rows]))
                  .values(status="expired"))
        s.commit()
        for r in rows:
            s.refresh(r)
            _broadcast("expired", r)
        return len(rows)


def tick() -> None:
    """Vom Backend-Ticker jede Sekunde aufgerufen; prüft höchstens einmal pro Minute."""
    global _last_tick
    now = time.time()
    if now - _last_tick < TICK_EVERY_S:
        return
    _last_tick = now
    try:
        n = expire_due()
        if n:
            log.info("%d Rückfrage(n) abgelaufen", n)
    except Exception:
        log.exception("Rückfragen: Verfall fehlgeschlagen")
