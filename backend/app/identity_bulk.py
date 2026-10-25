"""Identitäten 07 (Paket A): Sammel-Löschen, Sammel-Zusammenführen, Verlauf und Zurücksetzen – CONTRACT ID.B/ID.C.

- `bulk_delete(s, ids)` → gelöschte IDs. Eine Transaktion unter `identity._lock`; Art-Hooks (item/pet) wie beim
  Einzel-Löschen; Bilder nach dem Commit löschen; eine gebündelte WS-Nachricht
  `{"type":"identities","action":"deleted","ids":[…],"items":[{"id","kind"}]}`.
- `bulk_merge(s, ids, into_id, *, actor="bulk", score=None, report_id=None)` → `(Ziel, gemergte IDs, merge_ids)`.
  Immer vollständig; Zeilen `FOR UPDATE` in ID-Reihenfolge; kumulatives Limit manueller Bilder
  (`face_match.sample_limit`); Dateien nach dem Commit verschieben (Fehler → alten Pfad zurückschreiben);
  je Quelle ein `IdentityMerge`; WS `{"type":"identities","action":"merged","ids":[…],"into_id":N,"kind":…}` + Ziel.
  Wird auch vom Nachtlauf (`actor="nightly"`, `score` je Quelle, `report_id`) genutzt.
- `log_merge(s, src, dst, *, actor, score=None, report_id=None, sample_ids, event_ids)` → IdentityMerge
  (auch vom Einzel-Merge `identity.merge` und der Rückfrage „Ja“ genutzt; ohne Commit).
- `list_merges(s, identity_id=None, actor=None, undone=None, limit=50, offset=0)` → `{items, total}`.
- `undo_merge(s, merge_id)` → `(wiederhergestellte Identität, Ziel)`; Fehler als `BulkError(status, detail)`.

Grenzen beim Zurücksetzen (bewusst, siehe docs/plan/07-identitaeten-fortschritt.md): Bilder, die nach dem Merge
beim Kürzen der Galerie entfernt wurden, sind weg; Haustier-Tagebuch-Besuche und Gegenstands-Zuordnungen bleiben
beim Ziel (sie sind nicht je Quelle protokolliert); ein vom Ziel übernommener Name bleibt.
"""
from __future__ import annotations

import logging
import os
import shutil
from types import SimpleNamespace
from typing import Any, Optional, Union

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from . import identity as ident_svc
from . import settings_store
from .face_match import sample_limit
from .models import Event, Identity, IdentityMerge, IdentitySample, InventoryItem, utcnow
from .schemas import iso
from .serializers import identity_json
from .util import media_path
from .ws import hub

log = logging.getLogger("vg.identity_bulk")


class BulkError(ValueError):
    """Fehler mit HTTP-Status (404/409/422) und deutscher Meldung."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _lock_rows(s: Session, ids: list[int]) -> dict[int, Identity]:
    """Sperrt die Zeilen in ID-Reihenfolge (verhindert Verklemmungen) und meldet unbekannte IDs als 404."""
    rows = s.scalars(select(Identity).where(Identity.id.in_(ids)).order_by(Identity.id)
                     .with_for_update()).all()
    found = {r.id: r for r in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise BulkError(404, "Identität(en) nicht gefunden: " + ", ".join(str(i) for i in missing))
    return found


def _remove_files(paths: list[str]) -> None:
    for rel in paths:
        try:
            os.remove(media_path(rel))
        except (OSError, ValueError):
            pass


def _move_files(s: Session, moves: list[tuple[int, str, str]]) -> set[str]:
    """Verschiebt Bilddateien nach dem Commit. moves = [(sample_id, alter Pfad, neuer Pfad)].

    Schlägt ein Verschieben fehl, liegt die Datei noch am alten Ort → alten Pfad zurückschreiben.
    Rückgabe: Quellordner (`identities/<id>`), in denen noch Dateien liegen können.
    """
    failed: list[tuple[int, str]] = []
    keep_dirs: set[str] = set()
    for sid, old, new in moves:
        try:
            src_abs = media_path(old)
            if not os.path.exists(src_abs):
                continue   # Datei fehlte schon vorher – Pfad bleibt neu (wie beim Einzel-Merge)
            dst_abs = media_path(new)
            os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
            shutil.move(src_abs, dst_abs)
        except Exception as e:  # noqa: BLE001
            log.warning("Bilddatei nicht verschoben (Sample %s): %s", sid, e)
            failed.append((sid, old))
            keep_dirs.add(os.path.dirname(old))
    if failed:
        for sid, old in failed:
            s.execute(update(IdentitySample).where(IdentitySample.id == sid).values(image_path=old))
        s.commit()
    return keep_dirs


def _drop_dir(identity_id: int, keep_dirs: set[str]) -> None:
    rel = f"identities/{identity_id}"
    if rel in keep_dirs:
        try:   # nur entfernen, wenn leer (dort liegen noch referenzierte Dateien)
            os.rmdir(media_path(rel))
        except (OSError, ValueError):
            pass
    else:
        ident_svc.delete_identity_files(identity_id)


# ---------------------------------------------------------------- Löschen


def bulk_delete(s: Session, ids: list[int]) -> list[int]:
    uniq = sorted(set(int(i) for i in ids))
    if not uniq:
        raise BulkError(422, "Keine Identitäten ausgewählt")
    with ident_svc._lock:
        try:
            found = _lock_rows(s, uniq)
            kinds = {iid: found[iid].kind for iid in uniq}
            item_ids = [i for i in uniq if kinds[i] == "item"]
            if item_ids:   # Zuordnungen der Gegenstände lösen
                s.execute(update(InventoryItem).where(InventoryItem.identity_id.in_(item_ids))
                          .values(identity_id=None, identity_score=None))
            pet_ids = [i for i in uniq if kinds[i] == "pet"]
            if pet_ids:    # Tagebuch-Besuche bleiben als „unbekanntes Tier“
                from .pet_diary import on_identity_deleted
                for iid in pet_ids:
                    on_identity_deleted(s, iid)
            # Bilder außerhalb des eigenen Ordners (z. B. nach fehlgeschlagenem Verschieben) gezielt löschen
            stray = [p for iid, p in s.execute(select(IdentitySample.identity_id, IdentitySample.image_path).where(
                IdentitySample.identity_id.in_(uniq), IdentitySample.image_path.is_not(None))).all()
                if not p.startswith(f"identities/{iid}/")]
            for iid in uniq:
                s.delete(found[iid])   # Samples per FK CASCADE, Ereignisse per SET NULL
            s.commit()
        except BaseException:
            s.rollback()
            raise
        for iid in uniq:
            ident_svc.delete_identity_files(iid)
        _remove_files(stray)
        ident_svc.invalidate()
    if item_ids:
        from . import item_identity
        item_identity.changed()
    hub.broadcast({"type": "identities", "action": "deleted", "ids": uniq,
                   "items": [{"id": iid, "kind": kinds[iid]} for iid in uniq]})
    return uniq


# ---------------------------------------------------------------- Zusammenführen


def log_merge(s: Session, src: Identity, dst: Identity, *, actor: str, score: Optional[float] = None,
              report_id: Optional[int] = None, sample_ids: list[int], event_ids: list[str]) -> IdentityMerge:
    """Protokolliert eine vollständige Zusammenführung (vor dem Löschen der Quelle aufrufen; ohne Commit)."""
    m = IdentityMerge(
        ts=utcnow(), kind=src.kind, actor=actor, src_id=src.id, src_name=src.name,
        src_auto_number=src.auto_number or 0, src_notes=src.notes or "", src_first_seen=src.first_seen,
        src_last_seen=src.last_seen, src_learning_locked=bool(src.learning_locked),
        src_match_threshold=src.match_threshold, into_id=dst.id,
        score=None if score is None else float(score), sample_ids=[int(x) for x in sample_ids],
        event_ids=[str(x) for x in event_ids], nightly_report_id=report_id,
    )
    s.add(m)
    s.flush()
    return m


def _merge_fields(src: Identity, dst: Identity) -> None:
    """Name/Notizen/Zeiten wie beim klassischen Merge; ein abweichender Quellname landet als „früher: …“ in den Notizen."""
    if src.name:
        if not dst.name:
            dst.name = src.name
        elif src.name != dst.name:
            line = f"früher: {src.name}"
            if line not in (dst.notes or ""):
                dst.notes = ((dst.notes or "") + ("\n" if dst.notes else "") + line).strip()
    if src.notes and src.notes not in (dst.notes or ""):
        dst.notes = ((dst.notes or "") + ("\n" if dst.notes else "") + src.notes).strip()
    if src.first_seen and (not dst.first_seen or src.first_seen < dst.first_seen):
        dst.first_seen = src.first_seen
    if src.last_seen and (not dst.last_seen or src.last_seen > dst.last_seen):
        dst.last_seen = src.last_seen


def bulk_merge(s: Session, ids: list[int], into_id: int, *, actor: str = "bulk",
               score: Optional[Union[float, dict[int, float]]] = None,
               report_id: Optional[int] = None) -> tuple[Identity, list[int], list[int]]:
    """Führt alle `ids` vollständig in `into_id` zusammen – alles oder nichts.

    `score`: eine Zahl für alle Quellen oder `{src_id: score}` (Nachtlauf).
    """
    into_id = int(into_id)
    srcs = sorted(set(int(i) for i in ids) - {into_id})
    if not srcs:
        raise BulkError(422, "Keine Quellen ausgewählt")
    with ident_svc._lock:
        try:
            found = _lock_rows(s, sorted(set(srcs) | {into_id}))
            dst = found[into_id]
            if any(found[i].kind != dst.kind for i in srcs):
                raise BulkError(422, "Nur Identitäten gleicher Art können zusammengeführt werden")
            kind = dst.kind

            # Kumulatives Limit der manuellen Bilder (Ziel + alle Quellen); Name kann durch den Merge neu kommen
            named_after = dst.name or next((found[i].name for i in srcs if found[i].name), None)
            settings = settings_store.get()

            limit = sample_limit(SimpleNamespace(kind=kind, name=named_after), settings)
            manual = s.scalar(select(func.count()).select_from(IdentitySample).where(
                IdentitySample.identity_id.in_([into_id, *srcs]), IdentitySample.manual.is_(True))) or 0
            if manual > limit:
                raise BulkError(422, f"max. {limit} Bilder – erst alte löschen")

            samples = s.scalars(select(IdentitySample).where(IdentitySample.identity_id.in_(srcs))
                                .order_by(IdentitySample.id)).all()
            ev_rows = s.execute(select(Event.id, Event.identity_id).where(Event.identity_id.in_(srcs))).all()
            by_src_s: dict[int, list[IdentitySample]] = {i: [] for i in srcs}
            for smp in samples:
                by_src_s[smp.identity_id].append(smp)
            by_src_e: dict[int, list[str]] = {i: [] for i in srcs}
            for eid, iid in ev_rows:
                by_src_e[iid].append(eid)

            merge_ids: list[int] = []
            for sid in srcs:
                src = found[sid]
                sc = score.get(sid) if isinstance(score, dict) else score
                m = log_merge(s, src, dst, actor=actor, score=sc, report_id=report_id,
                              sample_ids=[x.id for x in by_src_s[sid]], event_ids=by_src_e[sid])
                merge_ids.append(m.id)
                _merge_fields(src, dst)
                for smp in by_src_s[sid]:
                    smp.identity_id = into_id
            s.execute(update(Event).where(Event.identity_id.in_(srcs)).values(identity_id=into_id))
            if kind == "pet":   # Besuche im Haustier-Tagebuch wandern mit
                from .pet_diary import on_identity_merged
                for sid in srcs:
                    on_identity_merged(s, sid, into_id)
            if kind == "item":
                s.execute(update(InventoryItem).where(InventoryItem.identity_id.in_(srcs))
                          .values(identity_id=into_id))
            s.flush()

            # Galerie kürzen (Dateien gelöschter Bilder liegen noch am alten Ort → nach dem Commit löschen)
            to_remove: list[str] = []
            pruned = set(ident_svc.prune_samples(s, into_id, sample_limit(dst, settings), defer_remove=to_remove))
            s.flush()
            # Neue Pfade der verbliebenen Bilder setzen, Verschieben erst nach dem Commit
            moves: list[tuple[int, str, str]] = []
            for smp in samples:
                if smp.id in pruned or not smp.image_path:
                    continue
                new = f"identities/{into_id}/{os.path.basename(smp.image_path)}"
                if new != smp.image_path:
                    moves.append((smp.id, smp.image_path, new))
                    smp.image_path = new
            s.flush()
            for sid in srcs:
                s.expire(found[sid])   # Sample-Liste nicht kaskadierend löschen (sie gehören jetzt dem Ziel)
                s.delete(found[sid])
            s.commit()
        except BaseException:
            s.rollback()
            raise
        keep_dirs = _move_files(s, moves)
        _remove_files(to_remove)
        for sid in srcs:
            _drop_dir(sid, keep_dirs)
        ident_svc.invalidate()
    if kind == "item":
        from . import item_identity
        item_identity.changed()
    hub.broadcast({"type": "identities", "action": "merged", "ids": srcs, "into_id": into_id, "kind": kind})
    hub.broadcast({"type": "identity", "identity": identity_json(s, dst)})
    return dst, srcs, merge_ids


# ---------------------------------------------------------------- Verlauf


def _src_display(m: IdentityMerge) -> str:
    probe = Identity(kind=m.kind, name=m.src_name, auto_number=m.src_auto_number or 0)
    return probe.display_name


def merge_json(m: IdentityMerge, targets: dict[int, Identity], existing: set[int]) -> dict[str, Any]:
    tgt = targets.get(m.into_id)
    return {
        "id": m.id, "ts": iso(m.ts), "kind": m.kind, "actor": m.actor,
        "src_id": m.src_id, "src_display_name": _src_display(m),
        "into_id": m.into_id, "into_display_name": tgt.display_name if tgt else None,
        "score": m.score, "sample_count": len(m.sample_ids or []), "event_count": len(m.event_ids or []),
        "nightly_report_id": m.nightly_report_id, "undone_at": iso(m.undone_at),
        "undoable": m.undone_at is None and tgt is not None and m.src_id not in existing,
    }


def list_merges(s: Session, identity_id: Optional[int] = None, actor: Optional[str] = None,
                undone: Optional[bool] = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    q = select(IdentityMerge)
    if identity_id is not None:
        q = q.where(or_(IdentityMerge.src_id == identity_id, IdentityMerge.into_id == identity_id))
    if actor:
        q = q.where(IdentityMerge.actor.in_([a.strip() for a in actor.split(",") if a.strip()]))
    if undone is True:
        q = q.where(IdentityMerge.undone_at.is_not(None))
    elif undone is False:
        q = q.where(IdentityMerge.undone_at.is_(None))
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = s.scalars(q.order_by(IdentityMerge.ts.desc(), IdentityMerge.id.desc()).limit(limit).offset(offset)).all()
    ids = {m.into_id for m in rows} | {m.src_id for m in rows}
    present = {i.id: i for i in s.scalars(select(Identity).where(Identity.id.in_(ids))).all()} if ids else {}
    return {"items": [merge_json(m, present, set(present)) for m in rows], "total": total}


def undo_merge(s: Session, merge_id: int) -> tuple[Identity, Identity]:
    with ident_svc._lock:
        try:
            m = s.get(IdentityMerge, merge_id, with_for_update=True)
            if m is None:
                raise BulkError(404, "Zusammenführung nicht gefunden")
            if m.undone_at is not None:
                raise BulkError(409, "Diese Zusammenführung wurde bereits zurückgesetzt")
            dst = s.get(Identity, m.into_id, with_for_update=True)
            if dst is None:
                raise BulkError(409, "Ziel existiert nicht mehr – zuerst die spätere Zusammenführung zurücksetzen")
            if s.get(Identity, m.src_id) is not None:
                raise BulkError(409, "Die Quell-Identität existiert bereits wieder")
            src = Identity(id=m.src_id, kind=m.kind, name=m.src_name, auto_number=m.src_auto_number or 0,
                           notes=m.src_notes or "", first_seen=m.src_first_seen or utcnow(),
                           last_seen=m.src_last_seen or utcnow(), learning_locked=bool(m.src_learning_locked),
                           match_threshold=m.src_match_threshold)
            s.add(src)
            s.flush()
            moves: list[tuple[int, str, str]] = []
            sample_ids = [int(x) for x in (m.sample_ids or [])]
            if sample_ids:
                for smp in s.scalars(select(IdentitySample).where(IdentitySample.id.in_(sample_ids),
                                                                  IdentitySample.identity_id == dst.id)).all():
                    smp.identity_id = src.id
                    if smp.image_path:
                        new = f"identities/{src.id}/{os.path.basename(smp.image_path)}"
                        if new != smp.image_path:
                            moves.append((smp.id, smp.image_path, new))
                            smp.image_path = new
            event_ids = [str(x) for x in (m.event_ids or [])]
            if event_ids:
                s.execute(update(Event).where(Event.id.in_(event_ids), Event.identity_id == dst.id)
                          .values(identity_id=src.id))
            # „früher: <Quellname>“ aus den Notizen des Ziels wieder entfernen
            if m.src_name and dst.notes:
                line = f"früher: {m.src_name}"
                dst.notes = "\n".join(x for x in dst.notes.split("\n") if x.strip() != line).strip()
            m.undone_at = utcnow()
            s.commit()
        except BaseException:
            s.rollback()
            raise
        keep_dirs = _move_files(s, moves)
        del keep_dirs   # Zielordner bleibt bestehen
        ident_svc.invalidate()
    if src.kind == "item":
        from . import item_identity
        item_identity.changed()
    hub.broadcast({"type": "identity", "identity": identity_json(s, src)})
    hub.broadcast({"type": "identity", "identity": identity_json(s, dst)})
    return src, dst
