"""Messwerkzeug Identitäten 07 (Paket E): Zersplitterung und Qualität der Gesichts-Identitäten – nur lesend.

Gibt nur Kennzahlen aus: keine Bilder, keine Namen, keine Notizen. Benannte Personen erscheinen nur als
„benannt #<id>“. Ergebnisse: docs/plan/07-identitaeten-fortschritt.md (Messung vorher / nach B+C / nach D).

Aufruf (im laufenden Backend-Container, liest DB und Bildgrößen aus dem Medien-Volume):
    docker compose exec -T backend python - < scripts/face_stats.py            # Text
    docker compose exec -T backend python - --json < scripts/face_stats.py     # zusätzlich JSON (letzte Zeile)

Kennzahlen (Zielwerte laut Plan Abschnitt E):
- Anteil Einzelbild-Identitäten (1 Bild) und 1–2 Bilder (Ziel: Einzelbild < 20 %)
- Fragmente je benannter Person: unbenannte mit Ähnlichkeit ≥ 0,40 (bzw. ≥ 0,55) zu ihr; je Tag Beobachtungszeit
  (Ziel ≤ 1 pro Tag)
- Verwechslung: maximale Ähnlichkeit zwischen zwei benannten Personen (Ziel: deutlich unter der Schwelle)
- Anteil Personen-Ereignisse mit Gesichts-Identität, Rückfragen identity_match mit „Nein“ (Fehlzuordnungen)
- Bildbreite/Qualität der Beispielbilder, Lebensdauer der Identitäten
Ähnlichkeit zweier Identitäten = Maximum über alle Bildpaare („max“) bzw. Mittel der besten 3 Paare („top3“).
"""
from __future__ import annotations

import collections
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import func, select

sys.path.insert(0, "/srv")
from app.config import DEFAULT_SETTINGS  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import KV, Event, Identity, IdentitySample, ReviewRequest  # noqa: E402


def pct(a, qs=(10, 50, 90)):
    a = np.asarray(a, float)
    return {f"p{q}": round(float(np.percentile(a, q)), 3) for q in qs} if len(a) else {}


def main() -> dict:
    now = datetime.now(timezone.utc)
    out: dict = {"ts": now.isoformat(timespec="seconds")}
    with SessionLocal() as s:
        row = s.get(KV, "settings")
        st = dict(DEFAULT_SETTINGS)
        st.update(row.value if row and isinstance(row.value, dict) else {})
        out["face_match_threshold"] = st.get("face_match_threshold")
        ids = s.execute(select(Identity).where(Identity.kind == "face")).scalars().all()
        named = {i.id for i in ids if i.name}
        out["identities"] = len(ids)
        out["named"] = len(named)
        out["locked"] = sum(1 for i in ids if i.learning_locked)
        rows = s.execute(
            select(IdentitySample.identity_id, IdentitySample.embedding, IdentitySample.quality,
                   IdentitySample.image_path, IdentitySample.negative)
            .join(Identity, Identity.id == IdentitySample.identity_id).where(Identity.kind == "face")).all()
        evc = dict(s.execute(select(Event.identity_id, func.count()).where(Event.identity_id.is_not(None))
                             .group_by(Event.identity_id)).all())
        pt = s.scalar(select(func.count()).select_from(Event).where(Event.label == "person")) or 0
        ptf = s.scalar(select(func.count()).select_from(Event).join(Identity, Event.identity_id == Identity.id)
                       .where(Event.label == "person", Identity.kind == "face")) or 0
        span = s.execute(select(func.min(Event.start_ts), func.max(Event.start_ts))
                         .where(Event.label == "person")).one()
        no_answers = s.scalar(select(func.count()).select_from(ReviewRequest).where(
            ReviewRequest.kind == "identity_match", ReviewRequest.status == "no")) or 0
        yes_answers = s.scalar(select(func.count()).select_from(ReviewRequest).where(
            ReviewRequest.kind == "identity_match", ReviewRequest.status == "yes")) or 0

    days = 0.0
    if span[0] and span[1]:
        days = max((span[1] - span[0]).total_seconds() / 86400, 1 / 24)
    out["person_events"] = pt
    out["person_events_with_face_identity"] = ptf
    out["share_person_events_identified"] = round(ptf / pt, 3) if pt else None
    out["observed_days"] = round(days, 2)
    out["identity_match_no"] = no_answers
    out["identity_match_yes"] = yes_answers

    by: dict[int, list[np.ndarray]] = collections.defaultdict(list)
    quality, widths, neg = [], [], 0
    md = os.environ.get("VG_MEDIA_DIR", "/data/media")
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        Image = None
    for iid, emb, q, path, ng in rows:
        if ng:
            neg += 1
            continue
        quality.append(float(q or 0))
        if path and Image is not None:
            try:
                with Image.open(os.path.join(md, path)) as im:   # nur Größe lesen, nichts ausgeben
                    widths.append(im.size[0])
            except Exception:  # noqa: BLE001
                pass
        if emb is None:
            continue
        v = np.asarray(emb, np.float32)
        n = float(np.linalg.norm(v))
        if n > 0:
            by[iid].append(v / n)
    out["samples"] = len(rows)
    out["negative_samples"] = neg
    sc = [len(by.get(i.id, [])) for i in ids]
    out["samples_per_identity"] = pct(sc)
    out["share_1_sample"] = round(sum(1 for x in sc if x == 1) / max(len(sc), 1), 3)
    out["share_1_2_samples"] = round(sum(1 for x in sc if 1 <= x <= 2) / max(len(sc), 1), 3)
    ec = [evc.get(i.id, 0) for i in ids]
    out["identities_without_events"] = sum(1 for x in ec if x == 0)
    life_h = [(i.last_seen - i.first_seen).total_seconds() / 3600 for i in ids if i.first_seen and i.last_seen]
    out["lifetime_h"] = pct(life_h)
    out["quality"] = pct(quality)
    out["image_width_px"] = pct(widths)

    keys = [k for k in (i.id for i in ids) if by.get(k)]
    n = len(keys)
    pmax = np.zeros((n, n), np.float32)
    ptop3 = np.zeros((n, n), np.float32)
    for a in range(n):
        A = np.stack(by[keys[a]])
        for b in range(a + 1, n):
            S = (A @ np.stack(by[keys[b]]).T).ravel()
            S.sort()
            pmax[a, b] = pmax[b, a] = float(S[-1])
            ptop3[a, b] = ptop3[b, a] = float(S[-3:].mean())
    intra = []
    for k in keys:
        M = np.stack(by[k])
        if len(M) > 1:
            S = M @ M.T
            intra.append(float(S[np.triu_indices(len(M), 1)].mean()))
    out["intra_mean_similarity"] = pct(intra)

    named_idx = [a for a, k in enumerate(keys) if k in named]
    per_named = []
    for a in named_idx:
        others = [b for b in range(n) if b != a and keys[b] not in named]
        per_named.append({
            "id": f"benannt #{keys[a]}",
            "samples": len(by[keys[a]]),
            "events": evc.get(keys[a], 0),
            "fragments_ge_040": int(sum(1 for b in others if pmax[a, b] >= 0.40)),
            "fragments_ge_055": int(sum(1 for b in others if pmax[a, b] >= 0.55)),
            "fragments_top3_ge_055": int(sum(1 for b in others if ptop3[a, b] >= 0.55)),
            "max_sim_other_named": round(float(max([pmax[a, b] for b in named_idx if b != a] or [0.0])), 3),
        })
    for x in per_named:
        x["fragments_ge_040_per_day"] = round(x["fragments_ge_040"] / days, 2) if days else None
    out["named_detail"] = per_named
    out["max_sim_between_named"] = round(float(max(
        [pmax[a, b] for a in named_idx for b in named_idx if a < b] or [0.0])), 3)

    # Unbenannte untereinander: Paare über Schwellen (Kandidaten für den Nachtlauf)
    un = [a for a in range(n) if keys[a] not in named]
    pairs = [(a, b) for i, a in enumerate(un) for b in un[i + 1:]]
    out["unnamed_pairs"] = len(pairs)
    for t in (0.40, 0.55):
        out[f"unnamed_pairs_max_ge_{int(t * 100):03d}"] = int(sum(1 for a, b in pairs if pmax[a, b] >= t))
        out[f"unnamed_pairs_top3_ge_{int(t * 100):03d}"] = int(sum(1 for a, b in pairs if ptop3[a, b] >= t))
    if n > 1:
        P = pmax.copy()
        np.fill_diagonal(P, -1)
        out["nearest_neighbour_max"] = pct(P.max(1))
    return out


def print_text(r: dict) -> None:
    print(f"Messung Gesichts-Identitäten ({r['ts']}), Schwelle {r['face_match_threshold']}")
    print(f"  Identitäten {r['identities']} (benannt {r['named']}, gesperrt {r['locked']}), "
          f"Bilder {r['samples']} (negativ {r['negative_samples']})")
    print(f"  Einzelbild {r['share_1_sample']:.0%}, 1–2 Bilder {r['share_1_2_samples']:.0%}, "
          f"ohne Ereignisse {r['identities_without_events']}, Lebensdauer h {r['lifetime_h']}")
    print(f"  Bildbreite px {r['image_width_px']}, Qualität {r['quality']}, Ähnlichkeit innen {r['intra_mean_similarity']}")
    share = r["share_person_events_identified"]
    print(f"  Personen-Ereignisse {r['person_events']}, davon mit Gesicht "
          f"{r['person_events_with_face_identity']} ({share if share is None else f'{share:.0%}'}), "
          f"Zeitraum {r['observed_days']} Tage")
    print(f"  Rückfragen identity_match: Ja {r['identity_match_yes']}, Nein {r['identity_match_no']}")
    print(f"  max. Ähnlichkeit zwischen Benannten {r['max_sim_between_named']}")
    for x in r["named_detail"]:
        print(f"  {x['id']}: Bilder {x['samples']}, Ereignisse {x['events']}, Fragmente ≥0,40 "
              f"{x['fragments_ge_040']} ({x['fragments_ge_040_per_day']}/Tag), ≥0,55 {x['fragments_ge_055']}, "
              f"top3≥0,55 {x['fragments_top3_ge_055']}, max. zu anderen Benannten {x['max_sim_other_named']}")
    print(f"  unbenannte Paare {r['unnamed_pairs']}: max≥0,40 {r['unnamed_pairs_max_ge_040']}, "
          f"max≥0,55 {r['unnamed_pairs_max_ge_055']}, top3≥0,40 {r['unnamed_pairs_top3_ge_040']}, "
          f"top3≥0,55 {r['unnamed_pairs_top3_ge_055']}")
    if "nearest_neighbour_max" in r:
        print(f"  nächster Nachbar (max) {r['nearest_neighbour_max']}")


if __name__ == "__main__":
    res = main()
    print_text(res)
    if "--json" in sys.argv[1:]:
        print(json.dumps(res, ensure_ascii=False))
