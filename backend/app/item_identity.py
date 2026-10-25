"""Exemplare eigener Gegenstände (B4): „Annas Rucksack“ statt nur „Rucksack“.

Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.D/W2.E. Exemplare sind Identitäten mit `kind="item"` und
`item_label` (Vokabular-Schlüssel). Samples, `manual`, Lernsperre, Merge und `prune_samples` stammen aus Phase 1.

- **Anlernen:** `POST /identities` (Name + Label), danach Fotos (`add_upload_sample`) oder der aktuelle Crop eines
  Gegenstands (`add_inventory_sample`). Das Embedding rechnet der Worker (`embed_client`, geteiltes DINOv2) mit
  Augmentierung (Original, gespiegelt, 120 px, 64 px) – gespeichert wird der L2-normierte Mittelwert.
- **Abgleich:** `identify_item` (Kern von `POST /api/internal/identify` mit `kind:"item"`), aufgerufen vom
  Inventar des Workers für Labels mit `instances > 0`. Nur innerhalb desselben `item_label`; ein negatives Sample,
  das ähnlicher ist als das beste positive, verhindert den Treffer; **nie** neue Identitäten.
- **Messwerte (messungen.md §3, „Bedingt Go“):** DINOv2 trennt Exemplare erst ab ≈ 90 px brauchbar. Crops, deren
  kürzere Seite unter `item_min_px` liegt, werden nie automatisch zugeordnet und lernen nie; im Band
  [Schwelle − 0,15, Schwelle) entsteht stattdessen eine Rückfrage (D2, `inventory_identity`).
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Any, Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import settings_store
from .models import Identity, IdentitySample, InventoryItem, utcnow
from .util import media_path

log = logging.getLogger("vg.item_identity")

NO_MATCH: dict[str, Any] = {"identity_id": None, "display_name": None, "name": None, "score": 0.0, "is_new": False}
LEARN_MARGIN = 0.10          # automatisch lernen erst ab Schwelle + 0,10
ASK_BAND = 0.15              # [Schwelle − 0,15, Schwelle) → Rückfrage statt Zuordnung (W2.B)
INVENTORY_CROP_MARGIN = 0.25  # Rand der Inventar-Crops im Worker (inventory._maybe_crop)
EMBED_MARGIN = 0.10          # Rand des Worker-Embeddings beim Abgleich (inventory.ItemMatcher)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_lock = threading.RLock()
# label → (Matrix [n,d] normiert, identity_ids [n], negative [n]) ; None = neu aufbauen
_gallery: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
_summaries: Optional[dict[int, dict[str, Any]]] = None


# ------------------------------------------------------------------ Caches


def invalidate() -> None:
    global _summaries
    with _lock:
        _gallery.clear()
        _summaries = None


def changed(publish: bool = True) -> None:
    """Nach jeder Änderung an Exemplaren/Samples: Caches leeren und den Worker neu laden lassen
    (`instances` in `/api/internal/cameras`; der Worker gleicht danach alle Gegenstände neu ab)."""
    invalidate()
    if publish:
        try:
            from .mqtt_bus import bus
            bus.publish_config_changed()
        except Exception:  # noqa: BLE001
            log.debug("config-Nachricht nicht gesendet", exc_info=True)


def summaries() -> dict[int, dict[str, Any]]:
    """{identity_id: {id, kind:"item", display_name, name, item_label}} aller Exemplare (gecacht)."""
    global _summaries
    with _lock:
        if _summaries is not None:
            return _summaries
    from .db import SessionLocal
    with SessionLocal() as s:
        rows = s.scalars(select(Identity).where(Identity.kind == "item")).all()
        data = {i.id: {"id": i.id, "kind": "item", "display_name": i.display_name, "name": i.name,
                       "item_label": i.item_label} for i in rows}
    with _lock:
        _summaries = data
    return data


def summary(identity_id: Optional[int]) -> Optional[dict[str, Any]]:
    if identity_id is None:
        return None
    d = summaries().get(int(identity_id))
    if d is None:
        return None
    return {k: d[k] for k in ("id", "kind", "display_name", "name")}


def _gallery_for(s: Session, label: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with _lock:
        g = _gallery.get(label)
        if g is not None:
            return g
    rows = s.execute(
        select(IdentitySample.identity_id, IdentitySample.embedding, IdentitySample.negative)
        .join(Identity, Identity.id == IdentitySample.identity_id)
        .where(Identity.kind == "item", Identity.item_label == label, IdentitySample.embedding.is_not(None))
    ).all()
    dims: dict[int, int] = {}
    for _, emb, _neg in rows:
        dims[len(emb)] = dims.get(len(emb), 0) + 1
    if not dims:
        g = (np.zeros((0, 0), np.float32), np.zeros((0,), np.int64), np.zeros((0,), bool))
    else:
        d = max(dims, key=dims.get)
        keep = [r for r in rows if len(r[1]) == d]
        mat = np.asarray([r[1] for r in keep], np.float32)
        n = np.linalg.norm(mat, axis=1, keepdims=True)
        n[n == 0] = 1.0
        g = (mat / n, np.asarray([r[0] for r in keep], np.int64), np.asarray([bool(r[2]) for r in keep], bool))
    with _lock:
        _gallery[label] = g
    return g


# ------------------------------------------------------------------ Abgleich


def score_identities(s: Session, label: str, vec: np.ndarray) -> list[tuple[float, int]]:
    """[(score, identity_id)] absteigend; Identitäten, bei denen ein negatives Sample ähnlicher ist als das beste
    positive, fallen weg."""
    mat, ids, neg = _gallery_for(s, label)
    if not mat.shape[0] or mat.shape[1] != vec.shape[0]:
        return []
    v = vec / max(float(np.linalg.norm(vec)), 1e-9)
    sims = mat @ v
    out = []
    for iid in np.unique(ids):
        m = ids == iid
        pos = sims[m & ~neg]
        if not pos.size:
            continue
        best_pos = float(pos.max())
        negs = sims[m & neg]
        if negs.size and float(negs.max()) >= best_pos:
            continue
        out.append((best_pos, int(iid)))
    out.sort(reverse=True)
    return out


def _find_inventory_row(s: Session, camera_id: Optional[int], item_id: Optional[str]) -> Optional[InventoryItem]:
    if camera_id is None or not item_id:
        return None
    return s.scalar(select(InventoryItem).where(InventoryItem.camera_id == camera_id,
                                                InventoryItem.worker_item_id == str(item_id))
                    .order_by(InventoryItem.updated_at.desc()).limit(1))


def identify_item(s: Session, req: Any) -> dict[str, Any]:
    """Kern von `POST /api/internal/identify` für `kind:"item"` (req: schemas.IdentifyIn mit label, item_id,
    crop_px). Legt nie eine neue Identität an; ohne Treffer `identity_id: None` (score = bester Wert)."""
    from .identity import _add_sample  # gemeinsame Sample-Verwaltung (MAX_SAMPLES, manual)

    label = (req.label or "").strip()
    if not label or not req.embedding:
        return dict(NO_MATCH)
    vec = np.asarray(req.embedding, dtype=np.float32)
    if not np.all(np.isfinite(vec)) or float(np.linalg.norm(vec)) == 0.0:
        return dict(NO_MATCH)
    st = settings_store.get()
    thr = float(st.get("item_match_threshold", 0.55))
    min_px = int(st.get("item_min_px", 90))
    crop_px = int(req.crop_px) if getattr(req, "crop_px", None) is not None else None
    big_enough = crop_px is None or crop_px >= min_px
    with _lock:
        ranked = score_identities(s, label, vec)
    if not ranked:
        return dict(NO_MATCH)
    score, iid = ranked[0]
    ident = s.get(Identity, iid)
    if ident is None:
        invalidate()
        return dict(NO_MATCH)
    out = dict(NO_MATCH)
    out["score"] = round(score, 4)
    log.debug("Exemplar-Abgleich Kamera %s, %s (%s px): bester Kandidat %s mit %.3f (Schwelle %.2f)",
             req.camera_id, label, crop_px, ident.display_name, score, thr)
    if score >= thr and big_enough:
        ident.last_seen = utcnow()
        learned = False
        if score >= thr + LEARN_MARGIN and not ident.learning_locked and req.crop_path:
            with _lock:
                learned = _add_sample(s, ident, [float(x) for x in vec], None, req.crop_path, score)
        s.commit()
        if learned:
            changed(publish=False)   # nur Galerie neu; der Worker muss deshalb nicht neu laden
        out.update({"identity_id": ident.id, "display_name": ident.display_name, "name": ident.name})
        return out
    # unsicher (knapp darunter oder Crop zu klein): Rückfrage statt Zuordnung
    if score >= thr - ASK_BAND:
        row = _find_inventory_row(s, req.camera_id, req.item_id)
        if row is not None:
            try:
                from . import review_effects
                review_effects.ask_item_identity(row, ident, score, thr, [float(x) for x in vec],
                                                 req.crop_path or row.crop_path, crop_px)
            except Exception:  # noqa: BLE001
                log.exception("Rückfrage Exemplar fehlgeschlagen")
    return out


# ------------------------------------------------------------------ Anlernen


class SampleError(Exception):
    """Ungültiger Upload (→ 422)."""


def sniff_image(data: bytes) -> Optional[str]:
    """jpeg/png/webp an den Magic Bytes erkennen → Dateiendung oder None."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def sample_json(smp: IdentitySample) -> dict[str, Any]:
    from .schemas import iso, media_url
    return {"id": smp.id, "image_url": media_url(smp.image_path), "created_at": iso(smp.created_at),
            "quality": smp.quality, "manual": bool(smp.manual), "negative": bool(smp.negative),
            "source": smp.source or "auto"}


def _manual_count(s: Session, identity_id: int) -> int:
    from sqlalchemy import func
    return s.scalar(select(func.count()).select_from(IdentitySample).where(
        IdentitySample.identity_id == identity_id, IdentitySample.manual.is_(True))) or 0


def _store_manual_sample(s: Session, ident: Identity, emb: list[float], image_path: Optional[str], *,
                         source: str, quality: float, negative: bool = False) -> IdentitySample:
    from .identity import MAX_SAMPLES, prune_samples
    if _manual_count(s, ident.id) >= MAX_SAMPLES:
        raise SampleError(f"max. {MAX_SAMPLES} Samples – erst alte löschen")
    smp = IdentitySample(identity_id=ident.id, embedding=[float(x) for x in emb], image_path=image_path,
                         quality=float(quality), manual=True, negative=negative, source=source)
    s.add(smp)
    s.flush()
    prune_samples(s, ident.id)
    ident.last_seen = ident.last_seen or utcnow()
    return smp


def add_upload_sample(s: Session, ident: Identity, data: bytes, bbox: Optional[list[float]] = None
                      ) -> dict[str, Any]:
    """Foto (jpeg/png/webp ≤ 10 MB) als manuelles Sample. Embedding mit Augmentierung im Worker; keine Antwort
    binnen 15 s → `embed_client.EmbedUnavailable` (Router: 503), dann bleibt nichts zurück."""
    from . import embed_client
    if ident.kind != "item":
        raise SampleError("Fotos können nur für eigene Gegenstände hochgeladen werden")
    if not data:
        raise SampleError("Leere Datei")
    if len(data) > MAX_UPLOAD_BYTES:
        raise SampleError("Datei zu groß (max. 10 MB)")
    ext = sniff_image(data)
    if ext is None:
        raise SampleError("Nur JPEG, PNG oder WebP")
    from .identity import MAX_SAMPLES
    if _manual_count(s, ident.id) >= MAX_SAMPLES:
        raise SampleError(f"max. {MAX_SAMPLES} Samples – erst alte löschen")
    rel = f"identities/{ident.id}/upload_{uuid.uuid4().hex[:12]}{ext}"
    full = media_path(rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)
    try:
        res = embed_client.request("item", path=rel, bbox=bbox, augment=True)
        smp = _store_manual_sample(s, ident, res.embedding, rel, source="upload", quality=1.0)
        s.commit()
    except BaseException:
        s.rollback()
        try:
            os.remove(full)
        except OSError:
            pass
        raise
    s.refresh(smp)
    changed()
    return sample_json(smp)


def inner_bbox_of_crop(item_bbox: list[float], margin: float = INVENTORY_CROP_MARGIN,
                       extra: float = EMBED_MARGIN) -> Optional[list[float]]:
    """Lage der (um `extra` erweiterten) Gegenstands-Box im Inventar-Crop (der mit Rand `margin` geschnitten
    wurde, an den Bildrändern abgeschnitten) – normiert auf den Crop."""
    try:
        x1, y1, x2, y2 = (float(v) for v in item_bbox)
    except (TypeError, ValueError):
        return None
    bw, bh = x2 - x1, y2 - y1
    if bw <= 0 or bh <= 0:
        return None
    cx1, cy1 = max(0.0, x1 - bw * margin), max(0.0, y1 - bh * margin)
    cx2, cy2 = min(1.0, x2 + bw * margin), min(1.0, y2 + bh * margin)
    ex1, ey1 = max(cx1, x1 - bw * extra), max(cy1, y1 - bh * extra)
    ex2, ey2 = min(cx2, x2 + bw * extra), min(cy2, y2 + bh * extra)
    cw, ch = cx2 - cx1, cy2 - cy1
    if cw <= 0 or ch <= 0:
        return None
    return [round((ex1 - cx1) / cw, 4), round((ey1 - cy1) / ch, 4), round((ex2 - cx1) / cw, 4),
            round((ey2 - cy1) / ch, 4)]


def add_inventory_sample(s: Session, ident: Identity, item: InventoryItem) -> dict[str, Any]:
    """Aktuellen Crop eines Gegenstands als manuelles Sample übernehmen („Von Kamera übernehmen“)."""
    from . import embed_client
    from .identity import _copy_crop
    if ident.kind != "item":
        raise SampleError("Nur für eigene Gegenstände")
    if ident.item_label and item.label != ident.item_label:
        raise SampleError("Gegenstand hat ein anderes Label als das Exemplar")
    if not item.crop_path:
        raise SampleError("Für diesen Gegenstand gibt es noch keinen Bildausschnitt")
    bbox = inner_bbox_of_crop(list(item.bbox or []))
    res = embed_client.request("item", path=item.crop_path, bbox=bbox, augment=False)
    img = _copy_crop(ident.id, item.crop_path)
    try:
        smp = _store_manual_sample(s, ident, res.embedding, img, source="upload",
                                   quality=float(item.score or 0.5))
        item.identity_id = ident.id
        s.commit()
    except BaseException:
        s.rollback()
        if img:
            from .util import safe_remove
            safe_remove(img)
        raise
    s.refresh(smp)
    changed()
    return sample_json(smp)


def add_review_sample(s: Session, ident: Identity, emb: Optional[list[float]], crop_path: Optional[str], *,
                      negative: bool, quality: float) -> Optional[IdentitySample]:
    """D2-Antwort auf `inventory_identity`: Crop als positives (Ja) bzw. negatives (Nein) manuelles Sample.
    Läuft in der Session der Antwort (Commit dort)."""
    from .identity import _copy_crop
    if not emb:
        return None
    img = _copy_crop(ident.id, crop_path)
    return _store_manual_sample(s, ident, emb, img, source="review", quality=quality, negative=negative)
