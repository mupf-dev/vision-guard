"""Lernen durch Rückfrage (D2): Erzeuger, Antwort-Wirkungen, Schwellen-Nachführung, Push und Antwort-Links.

Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.D. Der Kern (Dedup, Limits, Verfall, WS) steht in `review.py` (K2);
hier hängt D2 die fachliche Logik ein:

**Erzeuger**
- `inventory_label` aus dem Inventar (`on_inventory`, aufgerufen von `inventory.InventoryMemory` nach dem Commit):
  `uncertain` des Workers (Score knapp unter der Schwelle), erstes Auftauchen eines Labels auf einer Kamera, das dort
  noch nie bestätigt wurde, und sichtbare Gegenstände mit Score < Schwelle + `review_band` (einmal je Gegenstand).
  Dedup-Schlüssel `lbl:{cam}:{label}:{zelle}` (Zelle 10×10 des Mittelpunkts).
- `inventory_identity` aus dem Exemplar-Abgleich (`ask_item_identity`, B4): Score in [Schwelle − 0,15, Schwelle)
  oder Crop zu klein. Schlüssel `inst:{inventory_item_id}:{identity_id}`.
- `identity_match` alle 10 min (`tick`): unbenannte Gesichter/Tiere mit ≥ 2 Events und `likely`-Vorschlag.
  Schlüssel `idm:{src}:{dst}`.

**Wirkung der Antworten** (vor dem Commit der Antwort; Nacharbeiten nach dem Commit)
| Art | Ja | Nein |
|---|---|---|
| inventory_label | Positivbeispiel | Negativbeispiel, Unterdrückungsfläche, Gegenstand löschen – mit `correct_label` umetikettieren |
| inventory_identity | Crop als Sample (`source:"review"`, manual) + Zuordnung | Crop als negatives Sample |
| identity_match | klassischer Merge Quelle → Ziel | Paar gesperrt (Vorschläge lassen es weg) |

**Schwellen** (`review_auto_tune`, ab 3 Antworten je Kamera+Label): Mitte zwischen höchstem „Nein“- und niedrigstem
„Ja“-Score, höchstens ±0,05 je Antwort, Grenzen [Basis − 0,15, Basis + 0,20]. Ergebnis in
`cameras.inventory_label_min_scores`; welche Werte automatisch gesetzt wurden, steht in `kv["review_tune:{cam}"]`.
Weicht der Kamera-Wert davon ab, hat ihn jemand von Hand gesetzt → keine Nachführung (`auto:false`).

**Push/MQTT:** jede neue Frage → extern `{prefix}/review` `{review, answer_urls}` (signiert) und – wenn
`notify_routes.review` Ziele hat – eine Notification `source:"review"` mit Ja/Nein-Aktionen (ntfy).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import logging
import threading
import time
from typing import Any, Optional

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from . import inventory_names as names
from . import review, settings_store
from .config import config
from .db import SessionLocal
from .models import KV, Camera, Event, Identity, InventoryExample, InventoryItem, ReviewRequest
from .ws import hub

log = logging.getLogger("vg.review_effects")

MATCH_JOB_EVERY_S = 600.0
MAX_SUPPRESS = 200
TUNE_MIN_ANSWERS = 3
TUNE_STEP = 0.05
TUNE_BELOW = 0.15
TUNE_ABOVE = 0.20
NEW_LABEL_PRIORITY = 0.3

_last_match_job = 0.0
# Gegenstand → (nächste Prüfung, offener Grund): gefragt bzw. nichts zu fragen = inf; Limit/Dublette → in 10 min
_asked_items: dict[int, tuple[float, Optional[str]]] = {}
RETRY_S = 600.0
_asked_lock = threading.Lock()


# ------------------------------------------------------------------ Hilfen


def _vocab() -> dict[str, dict[str, Any]]:
    return names.vocab_map(settings_store.get().get("inventory_vocabulary") or [])


def name_de(label: str) -> str:
    return names.display_name(label, _vocab())


def cell_of(bbox: Optional[list[float]]) -> int:
    if not bbox or len(bbox) != 4:
        return 0
    cx = min(0.999, max(0.0, (float(bbox[0]) + float(bbox[2])) / 2))
    cy = min(0.999, max(0.0, (float(bbox[1]) + float(bbox[3])) / 2))
    return int(cy * 10) * 10 + int(cx * 10)


def base_threshold(cam: Camera, label: str) -> float:
    """Schwelle ohne Kamera-Wert je Label: Vokabular-Wert, sonst `inventory_min_score` der Kamera."""
    e = _vocab().get(label) or {}
    if e.get("min_score") is not None:
        return float(e["min_score"])
    return float(cam.inventory_min_score if cam.inventory_min_score is not None else 0.35)


def current_threshold(cam: Camera, label: str) -> float:
    v = (cam.inventory_label_min_scores or {}).get(label)
    return float(v) if v is not None else base_threshold(cam, label)


def _after_commit(s: Session, fn) -> None:
    """`fn()` nach dem nächsten erfolgreichen Commit der Session ausführen (Fehler nur loggen)."""
    def run(_session):
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("Nacharbeit nach Rückfrage-Antwort fehlgeschlagen")
    event.listen(s, "after_commit", run, once=True)


def _cameras_changed() -> None:
    from . import registry
    from .mqtt_bus import bus
    registry.reload()
    bus.publish_config_changed()


# ------------------------------------------------------------------ signierte Antwort-Links


def answer_sig(review_id: int, a: str, exp: int) -> str:
    mac = hmac.new(config.secret_key.encode("utf-8"), f"review:{int(review_id)}:{a}:{int(exp)}".encode("utf-8"),
                   hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def verify_answer_sig(review_id: int, a: str, exp: Any, sig: Optional[str]) -> bool:
    if a not in ("yes", "no") or not sig or exp is None:
        return False
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    return hmac.compare_digest(answer_sig(review_id, a, exp_i), str(sig))


def answer_urls(r: dict[str, Any]) -> dict[str, str]:
    """{yes, no}: signierte Antwort-Links (gültig bis `expires_at`), absolut wenn `public_base_url` gesetzt."""
    from datetime import datetime
    base = (settings_store.get().get("public_base_url") or "").rstrip("/")
    try:
        exp = int(datetime.fromisoformat(str(r["expires_at"]).replace("Z", "+00:00")).timestamp())
    except Exception:  # noqa: BLE001
        exp = int(time.time()) + 48 * 3600
    rid = int(r["id"])
    return {a: f"{base}/api/review/a/{rid}?a={a}&exp={exp}&sig={answer_sig(rid, a, exp)}" for a in ("yes", "no")}


# ------------------------------------------------------------------ Push / MQTT bei neuer Frage


def _on_created(r: dict[str, Any]) -> None:
    from . import media_sign
    st = settings_store.get()
    urls = answer_urls(r)
    try:
        from .mqtt_bus import bus
        bus.publish(f"{st['mqtt_prefix']}/review", {"review": media_sign.sign_urls(copy.deepcopy(r)),
                                                     "answer_urls": urls}, qos=1)
    except Exception as e:  # noqa: BLE001
        log.warning("MQTT {prefix}/review fehlgeschlagen: %s", e)
    if not (st.get("notify_routes") or {}).get("review"):
        return   # ohne Push-Ziel keine zusätzliche Meldung – die Frage steht ohnehin unter „Bitte bestätigen“
    from . import notifications
    base = (st.get("public_base_url") or "").rstrip("/")
    actions = []
    if base:   # Antwort-Knöpfe nur mit erreichbarer Adresse
        actions = [{"action": "http", "label": "Ja", "url": urls["yes"], "method": "POST", "clear": True},
                   {"action": "http", "label": "Nein", "url": urls["no"], "method": "POST", "clear": True}]
    where = f" ({r['camera_name']})" if r.get("camera_name") else ""
    try:
        notifications.create(source="review", level="info", title="Bitte bestätigen",
                             message=f"{r['question']}{where}", camera_id=r.get("camera_id"),
                             image_path=_rel(r.get("crop_url")), data={"review_id": r["id"], "kind": r["kind"]},
                             dedup_key=f"review:{r['id']}", dedup_s=60, actions=actions,
                             click_path="/bestaetigen")
    except Exception:  # noqa: BLE001
        log.exception("Push für Rückfrage %s fehlgeschlagen", r.get("id"))


def _rel(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    from .media_sign import normalize_path
    return normalize_path(url)


# ------------------------------------------------------------------ Erzeuger: Inventar


def _label_confirmed(s: Session, camera_id: int, label: str) -> bool:
    return s.scalar(select(ReviewRequest.id).where(
        ReviewRequest.kind == "inventory_label", ReviewRequest.camera_id == camera_id,
        ReviewRequest.label == label, ReviewRequest.status == "yes").limit(1)) is not None


LABEL_ASK_EVERY_S = 86400.0   # R1: je Label höchstens eine Rückfrage pro Tag (DB, übersteht Neustarts)


def _label_asked_recently(label: str) -> bool:
    """R1: Nach einem Neustart (Worker-Session neu, Speicher `_asked_items` leer) war das Tageslimit sonst in
    unter einer Minute verbraucht – mehrfach zum selben Label."""
    from datetime import datetime, timedelta, timezone
    since = datetime.now(timezone.utc) - timedelta(seconds=LABEL_ASK_EVERY_S)
    with SessionLocal() as s:
        return s.scalar(select(ReviewRequest.id).where(
            ReviewRequest.kind == "inventory_label", ReviewRequest.label == label,
            ReviewRequest.created_at >= since).limit(1)) is not None


def ask_label(*, camera_id: int, label: str, score: float, threshold: float, bbox: Optional[list[float]],
              crop_path: Optional[str], snapshot_path: Optional[str] = None,
              inventory_item_id: Optional[int] = None, reason: str = "uncertain",
              priority: Optional[float] = None) -> Optional[dict[str, Any]]:
    band = float(settings_store.get().get("review_band", 0.10))
    if _label_asked_recently(label):
        return None
    if priority is None:
        priority = max(0.0, 1.0 - abs(threshold - score) / max(band, 1e-3))
    return review.create(
        kind="inventory_label", dedup_key=f"lbl:{camera_id}:{label}:{cell_of(bbox)}",
        question=f"Erkannt als „{name_de(label)}“ – stimmt das?", priority=priority, camera_id=camera_id,
        label=label, inventory_item_id=inventory_item_id, score=round(float(score), 4),
        threshold=round(float(threshold), 4), bbox=bbox, crop_path=crop_path, snapshot_path=snapshot_path,
        data={"reason": reason})


def on_inventory(camera_id: int, msg: dict[str, Any], new_present: list[int]) -> None:
    """Nach dem Übernehmen einer Inventar-Nachricht (Track-Thread). `new_present` = IDs der Gegenstände, die in
    dieser Nachricht neu angelegt wurden."""
    st = settings_store.get()
    if not st.get("review_enabled", True) or int(st.get("review_max_per_day", 10)) <= 0:
        return
    band = float(st.get("review_band", 0.10))
    with SessionLocal() as s:
        cam = s.get(Camera, camera_id)
        if cam is None:
            return
        # 1) unsichere Detektionen des Workers
        for u in (msg.get("uncertain") or [])[:3]:
            if not isinstance(u, dict) or not u.get("label") or not u.get("crop_path"):
                continue
            try:
                sc = float(u.get("score") or 0)
                bbox = [float(x) for x in u.get("bbox") or []]
            except (TypeError, ValueError):
                continue
            ask_label(camera_id=camera_id, label=str(u["label"]), score=sc,
                      threshold=current_threshold(cam, str(u["label"])), bbox=bbox if len(bbox) == 4 else None,
                      crop_path=str(u["crop_path"]), reason="uncertain")
        # 2) neue Gegenstände: Label auf dieser Kamera noch nie bestätigt, oder Score knapp über der Schwelle
        ids = set(new_present)
        rows = list(s.scalars(select(InventoryItem).where(InventoryItem.camera_id == camera_id,
                                                          InventoryItem.state == "present")).all())
        now = time.time()
        for r in rows:
            with _asked_lock:
                retry_at, pending = _asked_items.get(r.id, (0.0, None))
            if now < retry_at:
                continue
            thr = current_threshold(cam, r.label)
            sc = float(r.score or 0)
            reason = None
            if sc < thr + band:
                reason = "low_score"
            elif (r.id in ids or pending == "new_label") and not _label_confirmed(s, camera_id, r.label):
                others = s.scalar(select(func.count()).select_from(InventoryItem).where(
                    InventoryItem.camera_id == camera_id, InventoryItem.label == r.label,
                    InventoryItem.id != r.id)) or 0
                if others == 0:
                    reason = "new_label"
            if reason is None:
                with _asked_lock:
                    _asked_items[r.id] = (float("inf"), None)
                continue
            if not r.crop_path:      # noch kein Bild → bald erneut prüfen
                with _asked_lock:
                    _asked_items[r.id] = (now + 60, reason)
                continue
            res = ask_label(camera_id=camera_id, label=r.label, score=sc, threshold=thr, bbox=list(r.bbox or []),
                            crop_path=r.crop_path, snapshot_path=r.snapshot_path, inventory_item_id=r.id,
                            reason=reason, priority=NEW_LABEL_PRIORITY if reason == "new_label" else None)
            with _asked_lock:   # Limit/Dublette: in 10 min erneut versuchen (Tageslimit kann sich ändern)
                _asked_items[r.id] = (float("inf"), None) if res else (now + RETRY_S, reason)


def forget_item(item_id: int) -> None:
    with _asked_lock:
        _asked_items.pop(item_id, None)


# ------------------------------------------------------------------ Erzeuger: Exemplare (B4)


def ask_item_identity(row: InventoryItem, ident: Identity, score: float, thr: float, embedding: list[float],
                      crop_path: Optional[str], crop_px: Optional[int]) -> Optional[dict[str, Any]]:
    small = crop_px is not None and crop_px < int(settings_store.get().get("item_min_px", 90))
    q = f"Ist das „{ident.display_name}“?"
    return review.create(
        kind="inventory_identity", dedup_key=f"inst:{row.id}:{ident.id}", question=q,
        priority=max(0.0, 1.0 - (thr - score) / 0.15) if score < thr else 0.9,
        camera_id=row.camera_id, label=row.label, inventory_item_id=row.id, identity_id=ident.id,
        score=round(float(score), 4), threshold=round(float(thr), 4), bbox=list(row.bbox or []) or None,
        crop_path=crop_path, snapshot_path=row.snapshot_path,
        data={"embedding": [round(float(x), 6) for x in embedding], "crop_px": crop_px, "small": small})


# ------------------------------------------------------------------ Erzeuger: Gesichter/Tiere (alle 10 min)


def _match_job() -> int:
    from .identity import best_suggestions
    from .models import IdentitySample
    created = 0
    with SessionLocal() as s:
        ev_counts = select(Event.identity_id, func.count().label("n")).where(
            Event.identity_id.is_not(None)).group_by(Event.identity_id).subquery()
        cands = list(s.scalars(
            select(Identity).join(ev_counts, ev_counts.c.identity_id == Identity.id)
            .where(Identity.kind.in_(("face", "pet")), (Identity.name.is_(None)) | (Identity.name == ""),
                   ev_counts.c.n >= 2)
            .order_by(Identity.last_seen.desc()).limit(200)).all())
        if not cands:
            return 0
        best = best_suggestions(s, cands)
        for src in cands:
            sg = best.get(src.id)
            if not sg:
                continue
            dst_id = int(sg["identity_id"])
            covers = dict(s.execute(
                select(IdentitySample.identity_id, func.max(IdentitySample.image_path))
                .where(IdentitySample.identity_id.in_([src.id, dst_id]), IdentitySample.image_path.is_not(None),
                       IdentitySample.negative.is_(False))
                .group_by(IdentitySample.identity_id)).all())
            what = "dieselbe Person" if src.kind == "face" else "dasselbe Tier"
            r = review.create(
                kind="identity_match", dedup_key=f"idm:{src.id}:{dst_id}",
                question=f"Ist „{src.display_name}“ {what} wie „{sg['display_name']}“?",
                priority=float(sg["score"]), identity_id=dst_id, source_identity_id=src.id,
                score=float(sg["score"]), crop_path=covers.get(src.id), snapshot_path=covers.get(dst_id),
                data={"identity_kind": src.kind, "target_name": sg["display_name"],
                      "source_name": src.display_name})
            if r:
                created += 1
    return created


def tick() -> None:
    """Vom Backend-Ticker jede Sekunde aufgerufen; der Vorschlags-Job läuft alle 10 min."""
    global _last_match_job
    now = time.time()
    if now - _last_match_job < MATCH_JOB_EVERY_S:
        return
    _last_match_job = now
    st = settings_store.get()
    if not st.get("review_enabled", True):
        return
    n = _match_job()
    if n:
        log.info("%d Rückfrage(n) zu Gesichter-/Tier-Vorschlägen angelegt", n)


# ------------------------------------------------------------------ Antwort-Wirkungen


def _answer_label(s: Session, r: ReviewRequest, answer: str, correct_label: Optional[str]) -> None:
    vocab = _vocab()
    if correct_label and correct_label not in vocab:
        raise review.ReviewError(422, f"Unbekannter Gegenstand: {correct_label}")
    if correct_label == r.label:
        correct_label = None
    yes = answer == "yes"
    if r.label:
        s.add(InventoryExample(label=r.label, camera_id=r.camera_id, crop_path=r.crop_path, bbox=r.bbox,
                               positive=yes, review_id=r.id))
    if not yes and correct_label:
        s.add(InventoryExample(label=correct_label, camera_id=r.camera_id, crop_path=r.crop_path, bbox=r.bbox,
                               positive=True, review_id=r.id))
    cam = s.get(Camera, r.camera_id) if r.camera_id is not None else None
    deleted_item: Optional[dict[str, Any]] = None
    relabeled_id: Optional[int] = None
    if not yes and cam is not None:
        if r.bbox and r.label:
            sup = [x for x in (cam.inventory_suppress or []) if isinstance(x, dict)]
            sup.append({"label": r.label, "bbox": [round(float(v), 4) for v in r.bbox], "review_id": r.id})
            cam.inventory_suppress = sup[-MAX_SUPPRESS:]
        item = s.get(InventoryItem, r.inventory_item_id) if r.inventory_item_id else None
        if item is not None and item.camera_id == cam.id:
            if correct_label:
                # Umetikettieren: der Worker meldet das alte Label dort nicht mehr (Unterdrückung); die Zeile behält
                # Ort, Bild und Verlauf unter dem richtigen Namen (ohne Worker-Verknüpfung → „zuletzt gesehen“)
                item.label = correct_label
                item.worker_item_id = None
                item.relation = None if correct_label in names.SURFACE_LABELS else item.relation
                relabeled_id = item.id
            else:
                from .inventory import item_json
                deleted_item = item_json(item)
                _drop_item(s, item)
    cam_id = cam.id if cam is not None else None
    label = r.label

    def after() -> None:
        if cam_id is not None and not yes:
            _cameras_changed()
        if deleted_item:
            forget_item(deleted_item["id"])
            hub.broadcast({"type": "inventory", "camera_id": deleted_item["camera_id"], "action": "delete",
                           "items": [deleted_item]})
        if relabeled_id is not None:
            _broadcast_item(relabeled_id)
        if cam_id is not None and label:
            tune(cam_id, label)
    _after_commit(s, after)


def _drop_item(s: Session, item: InventoryItem) -> None:
    """Wie `DELETE /inventory/{id}` (Historie mit), aber in der Session der Antwort."""
    from sqlalchemy import delete

    from .inventory import memory
    from .models import InventoryHistory
    with memory.lock:
        if item.worker_session and item.worker_item_id:
            memory.suppressed.add((item.camera_id, item.worker_session, item.worker_item_id))
        memory.last_history.pop(item.id, None)
        memory.last_ws.pop(item.id, None)
    s.execute(delete(InventoryHistory).where(InventoryHistory.item_id == item.id))
    s.delete(item)


def _broadcast_item(item_id: int) -> None:
    from .inventory import item_json
    with SessionLocal() as s2:
        it = s2.get(InventoryItem, item_id)
        if it is not None:
            hub.broadcast({"type": "inventory", "camera_id": it.camera_id, "action": "update",
                           "items": [item_json(it)]})


def _answer_identity(s: Session, r: ReviewRequest, answer: str, correct_label: Optional[str]) -> None:
    from . import item_identity
    ident = s.get(Identity, r.identity_id) if r.identity_id else None
    if ident is None:
        raise review.ReviewError(409, "Das Exemplar existiert nicht mehr")
    yes = answer == "yes"
    emb = (r.data or {}).get("embedding")
    try:
        item_identity.add_review_sample(s, ident, emb, r.crop_path, negative=not yes,
                                        quality=float(r.score or 0.5))
    except item_identity.SampleError as e:
        raise review.ReviewError(422, str(e)) from None
    item_id = None
    if yes and r.inventory_item_id:
        item = s.get(InventoryItem, r.inventory_item_id)
        if item is not None:
            item.identity_id = ident.id
            item.identity_score = r.score
            item_id = item.id

    def after() -> None:
        item_identity.changed()
        if item_id is not None:
            _broadcast_item(item_id)
    _after_commit(s, after)


def _answer_match(s: Session, r: ReviewRequest, answer: str, correct_label: Optional[str]) -> None:
    if answer != "yes":
        return   # „Nein“: der Dedup-Schlüssel sperrt das Paar; identity._suggest_for lässt es weg
    from . import identity as ident_svc
    from .serializers import identity_json
    src = s.get(Identity, r.source_identity_id) if r.source_identity_id else None
    dst = s.get(Identity, r.identity_id) if r.identity_id else None
    if src is None or dst is None:
        raise review.ReviewError(409, "Eine der Identitäten existiert nicht mehr")
    if src.kind != dst.kind:
        raise review.ReviewError(422, "Nur Identitäten gleicher Art können zusammengeführt werden")
    src_id, kind = src.id, src.kind
    try:
        dst, _ = ident_svc.merge(s, src, dst, None)   # committet selbst (inkl. Status der Rückfrage)
    except ident_svc.MergeError as e:
        raise review.ReviewError(422, str(e)) from None
    hub.broadcast({"type": "identity", "identity": {"id": src_id, "kind": kind, "deleted": True,
                                                     "merged_into": dst.id}})
    hub.broadcast({"type": "identity", "identity": identity_json(s, dst)})


review.register_kind("inventory_label", on_answer=_answer_label)
review.register_kind("inventory_identity", on_answer=_answer_identity)
review.register_kind("identity_match", on_answer=_answer_match)
review.on_created(_on_created)


# ------------------------------------------------------------------ Schwellen


def _tune_key(camera_id: int) -> str:
    return f"review_tune:{camera_id}"


def _answers(s: Session, camera_id: int, label: Optional[str] = None) -> list[tuple[str, str, float]]:
    q = select(ReviewRequest.label, ReviewRequest.status, ReviewRequest.score).where(
        ReviewRequest.kind == "inventory_label", ReviewRequest.camera_id == camera_id,
        ReviewRequest.status.in_(("yes", "no")), ReviewRequest.score.is_not(None))
    if label:
        q = q.where(ReviewRequest.label == label)
    return [(lb, st, float(sc)) for lb, st, sc in s.execute(q).all() if lb]


def tune(camera_id: int, label: str) -> Optional[float]:
    """Schwelle für Kamera+Label nachführen (siehe Modul-Doku). Rückgabe: neuer Wert oder None."""
    if not settings_store.get().get("review_auto_tune", True):
        return None
    with SessionLocal() as s:
        cam = s.get(Camera, camera_id)
        if cam is None:
            return None
        rows = _answers(s, camera_id, label)
        if len(rows) < TUNE_MIN_ANSWERS:
            return None
        mins = dict(cam.inventory_label_min_scores or {})
        kv = s.get(KV, _tune_key(camera_id), with_for_update=True)
        auto_vals = dict(kv.value or {}) if kv is not None and isinstance(kv.value, dict) else {}
        cur_set = mins.get(label)
        if cur_set is not None and (label not in auto_vals or abs(float(auto_vals[label]) - float(cur_set)) > 1e-6):
            return None   # von Hand gesetzt → Vorrang
        base = base_threshold(cam, label)
        cur = float(cur_set) if cur_set is not None else base
        yes = [sc for _, st, sc in rows if st == "yes"]
        no = [sc for _, st, sc in rows if st == "no"]
        if yes and no:
            target = (max(no) + min(yes)) / 2
        elif yes:
            target = min(yes) - 0.02
        else:
            target = max(no) + 0.02
        lo, hi = max(0.05, base - TUNE_BELOW), min(0.95, base + TUNE_ABOVE)
        target = min(hi, max(lo, target))
        new = round(cur + max(-TUNE_STEP, min(TUNE_STEP, target - cur)), 3)
        new = min(hi, max(lo, new))
        if abs(new - cur) < 0.005:
            return None
        mins[label] = new
        cam.inventory_label_min_scores = mins
        auto_vals[label] = new
        if kv is None:
            s.add(KV(key=_tune_key(camera_id), value=auto_vals))
        else:
            kv.value = auto_vals
        s.commit()
    log.info("Kamera %s: Schwelle für %s nachgeführt %.3f → %.3f (%d Antworten)", camera_id, label, cur, new,
             len(rows))
    _cameras_changed()
    return new


def thresholds(s: Session, camera_id: Optional[int] = None) -> list[dict[str, Any]]:
    """[{camera_id, label, name_de, base, current, auto, yes, no}] für Labels mit Antworten oder eigener Schwelle."""
    q = select(Camera).order_by(Camera.id)
    if camera_id is not None:
        q = q.where(Camera.id == camera_id)
    out: list[dict[str, Any]] = []
    for cam in s.scalars(q).all():
        kv = s.get(KV, _tune_key(cam.id))
        auto_vals = dict(kv.value or {}) if kv is not None and isinstance(kv.value, dict) else {}
        mins = dict(cam.inventory_label_min_scores or {})
        counts: dict[str, list[int]] = {}
        for lb, st, _ in _answers(s, cam.id):
            c = counts.setdefault(lb, [0, 0])
            c[0 if st == "yes" else 1] += 1
        for lb in sorted(set(counts) | set(mins), key=lambda x: name_de(x).lower()):
            base = base_threshold(cam, lb)
            cur = mins.get(lb)
            auto = cur is None or (lb in auto_vals and abs(float(auto_vals[lb]) - float(cur)) <= 1e-6)
            c = counts.get(lb, [0, 0])
            out.append({"camera_id": cam.id, "label": lb, "name_de": name_de(lb), "base": round(base, 3),
                        "current": round(float(cur) if cur is not None else base, 3), "auto": auto,
                        "yes": c[0], "no": c[1]})
    return out
