"""Identitäten 07 (Paket C): Gesichts-Abgleich und Galerie-Auswahl – CONTRACT ID.D/ID.F.

Gemeinsame Bausteine für `identity.identify` (Paket C) und den Nachtlauf (`nightly.py`, Paket D):

- `top_k_scores(mat, ids, vec, k=3)` → `{identity_id: score}`: Score je Identität = Mittel der besten `k`
  Bild-Ähnlichkeiten (Kosinus, Embeddings L2-normiert; bei < k Bildern das Mittel der vorhandenen).
- `decide(scores, thresholds, margin)` → `(identity_id | None, score, second_score, decision)` mit
  `decision ∈ {"match", "none", "ambiguous"}`: Treffer, wenn `score ≥ Schwelle der Identität` und
  `score − second_score ≥ margin`. Bei "none"/"ambiguous" wird trotzdem die ID des Besten geliefert (für
  Rückfragen); zugeordnet wird nur bei "match".
- `threshold_for(ident, settings)` → `ident.match_threshold` ?? `face_match_threshold` bzw. `pet_match_threshold`.
- `sample_limit(ident, settings)` → `face_max_samples_named` für benannte Gesichter, sonst `identity.MAX_SAMPLES`.
- `select_gallery(samples, limit)` → IDs der zu behaltenden Bilder: manuelle immer, sonst k-Center über die
  Embeddings (Start: bestes Bild bzw. die manuellen Bilder), Tag/Nacht getrennt anteilig; Profile/unscharfe Bilder
  und Ausreißer zuletzt.
- `is_poor(sample)` → Profil (|Gier| > 40°, |Nicken| > 30°) oder unscharf (Laplace-Varianz < 25). Solche Bilder
  speichert `identity` nie als neues Beispiel; fehlende Werte (Altdaten, alte Worker) gelten als brauchbar.
- `mean_embedding(vectors)` → L2-normiertes Mittel (Track-Mittel, falls der Worker kein `embedding` schickt).

Alle Funktionen sind rein (keine DB), damit Nachtlauf und Tests sie direkt nutzen können.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import numpy as np

TOP_K = 3

# Profil-/Unschärfe-Grenzen. Bewusst Konstanten statt Settings: sie beschreiben das Modellverhalten (ArcFace wird
# bei starker Kopfdrehung bzw. Unschärfe unzuverlässig), keine Vorliebe des Nutzers.
MAX_YAW = 40.0
MAX_PITCH = 30.0
MIN_SHARPNESS = 25.0


def threshold_for(ident: Any, settings: dict[str, Any]) -> float:
    if getattr(ident, "match_threshold", None) is not None:
        return float(ident.match_threshold)
    kind = getattr(ident, "kind", "face")
    return float(settings["face_match_threshold"] if kind == "face" else settings["pet_match_threshold"])


def sample_limit(ident: Any, settings: dict[str, Any]) -> int:
    from .identity import MAX_SAMPLES
    if getattr(ident, "kind", None) == "face" and getattr(ident, "name", None):
        return int(settings.get("face_max_samples_named", 40))
    return MAX_SAMPLES


def _norm(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n = np.where(n == 0, 1.0, n)
    return v / n


def top_k_scores(mat: np.ndarray, ids: np.ndarray, vec: np.ndarray, k: int = TOP_K) -> dict[int, float]:
    """Score je Identität = Mittel der besten k Kosinus-Ähnlichkeiten (`mat`-Zeilen L2-normiert)."""
    vec = np.asarray(vec, dtype=np.float32)
    if mat is None or mat.ndim != 2 or not mat.shape[0] or mat.shape[1] != vec.shape[-1]:
        return {}
    sims = mat @ _norm(vec)
    ids = np.asarray(ids)
    order = np.lexsort((-sims, ids))            # nach ID gruppiert, innerhalb absteigend
    sids, ssims = ids[order], sims[order]
    uniq, start = np.unique(sids, return_index=True)
    ends = list(start[1:]) + [len(sids)]
    k = max(1, int(k))
    return {int(u): float(ssims[a:min(b, a + k)].mean()) for u, a, b in zip(uniq, start, ends)}


def decide(scores: dict[int, float], thresholds: dict[int, float], margin: float
           ) -> tuple[Optional[int], float, Optional[float], str]:
    """Entscheidung aus den Scores. `thresholds` muss die Schwelle des Besten enthalten (fehlt sie, gilt die
    kleinste übergebene, ohne Angaben 1.0 = nie). Rückgabe (id des Besten | None, bester Score, zweitbester | None,
    decision)."""
    if not scores:
        return None, 0.0, None, "none"
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_id, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else None
    thr = thresholds.get(best_id)
    if thr is None:
        thr = min(thresholds.values()) if thresholds else 1.0
    if best < thr:
        return best_id, best, second, "none"
    if second is not None and best - second < margin:
        return best_id, best, second, "ambiguous"
    return best_id, best, second, "match"


def _get(x: Any, name: str, default: Any = None) -> Any:
    if isinstance(x, dict):
        return x.get(name, default)
    return getattr(x, name, default)


def is_poor(sample: Any) -> bool:
    """Profil oder unscharf? Unbekannte Werte (None) zählen nicht dagegen."""
    yaw, pitch, sharp = _get(sample, "yaw"), _get(sample, "pitch"), _get(sample, "sharpness")
    if yaw is not None and abs(float(yaw)) > MAX_YAW:
        return True
    if pitch is not None and abs(float(pitch)) > MAX_PITCH:
        return True
    if sharp is not None and float(sharp) < MIN_SHARPNESS:
        return True
    return False


def _emb(x: Any) -> Optional[list]:
    e = _get(x, "embedding")
    return e if e is not None and len(e) > 0 else None


def _k_center(emb: np.ndarray, quality: np.ndarray, centers: list[np.ndarray], n: int) -> list[int]:
    """Greedy k-Center: wählt bis zu n Indizes. Ohne vorhandene Zentren startet es beim besten Bild.
    Abstand = 1 − Kosinus, gewichtet mit (0,5 + 0,5·Qualität), damit nicht das schlechteste Bild gewinnt."""
    chosen: list[int] = []
    if n <= 0 or not emb.shape[0]:
        return chosen
    w = 0.5 + 0.5 * np.clip(quality.astype(np.float64), 0, 1)
    if centers:
        mind = (1.0 - emb @ np.stack(centers).T).min(axis=1).astype(np.float64)
    else:
        first = int(np.argmax(quality))
        chosen.append(first)
        mind = (1.0 - emb @ emb[first]).astype(np.float64)
    while len(chosen) < min(n, emb.shape[0]):
        score = mind * w
        score[chosen] = -np.inf
        nxt = int(np.argmax(score))
        if not np.isfinite(score[nxt]):
            break
        chosen.append(nxt)
        mind = np.minimum(mind, 1.0 - emb @ emb[nxt])
    return chosen


def select_gallery(samples: list[Any], limit: int) -> list[int]:
    """IDs der zu behaltenden Bilder (Objekte oder Dicts mit id, embedding, quality, manual, negative, day_night,
    yaw, pitch, sharpness).

    - Negative Bilder werden nicht betrachtet (nicht in der Rückgabe; Aufrufer lassen sie unangetastet).
    - Manuelle Bilder bleiben immer (auch über `limit`) und dienen als Startzentren.
    - Freie Plätze: zuerst brauchbare Bilder (nicht `is_poor`, kein Ausreißer), Tag/Nacht getrennt anteilig (je
      nicht-leerer Gruppe mindestens 1 Platz), innerhalb jeder Gruppe k-Center; übrige Plätze k-Center über alle
      brauchbaren. Ausreißer = Ähnlichkeit zum Zentroid > 0,25 unter dem Median (ab 5 Bildern).
    - Danach Profil-/unscharfe Bilder und Ausreißer nach Qualität, zuletzt Bilder ohne (passendes) Embedding.
    """
    pos = [x for x in samples if not _get(x, "negative", False)]
    manual = [x for x in pos if _get(x, "manual", False)]
    keep: list[int] = [int(_get(x, "id")) for x in manual]
    free = int(limit) - len(keep)
    rest = [x for x in pos if not _get(x, "manual", False)]
    if free <= 0 or not rest:
        return keep

    dims: dict[int, int] = {}
    for x in pos:
        e = _emb(x)
        if e is not None:
            dims[len(e)] = dims.get(len(e), 0) + 1
    d = max(dims, key=dims.get) if dims else -1
    with_emb = [x for x in rest if _emb(x) is not None and len(_emb(x)) == d]
    weak_tail = [x for x in rest if _emb(x) is None or len(_emb(x)) != d]

    outlier: set[int] = set()
    allv = [x for x in pos if _emb(x) is not None and len(_emb(x)) == d]
    if len(allv) >= 5:
        m = _norm(np.asarray([_emb(x) for x in allv], dtype=np.float32))
        sims = m @ _norm(m.mean(axis=0))
        med = float(np.median(sims))
        outlier = {id(x) for x, sv in zip(allv, sims) if float(sv) < med - 0.25}

    good = [x for x in with_emb if not is_poor(x) and id(x) not in outlier]
    poor = [x for x in with_emb if is_poor(x) or id(x) in outlier]

    def group(x: Any) -> str:
        return "night" if _get(x, "day_night") == "night" else "day"   # unbekannt zählt als Tag

    groups: dict[str, list[Any]] = {}
    for x in good:
        groups.setdefault(group(x), []).append(x)
    budget = min(free, len(good))
    alloc = {g: max(1, int(round(budget * len(xs) / len(good)))) for g, xs in groups.items()} if good else {}
    while alloc and sum(alloc.values()) > budget:
        g = max(alloc, key=lambda k: alloc[k])
        alloc[g] -= 1

    def man_centers(g: Optional[str]) -> list[np.ndarray]:
        return [_norm(np.asarray(_emb(mx), dtype=np.float32)) for mx in manual
                if (g is None or group(mx) == g) and _emb(mx) is not None and len(_emb(mx)) == d]

    chosen: list[Any] = []
    leftovers: list[Any] = []
    for g, xs in groups.items():
        emb = _norm(np.asarray([_emb(x) for x in xs], dtype=np.float32))
        q = np.asarray([float(_get(x, "quality") or 0) for x in xs], dtype=np.float32)
        idx = _k_center(emb, q, man_centers(g), alloc.get(g, 0))
        chosen += [xs[i] for i in idx]
        sel = set(idx)
        leftovers += [xs[i] for i in range(len(xs)) if i not in sel]

    free_after = free - len(chosen)
    if free_after > 0 and leftovers:
        emb = _norm(np.asarray([_emb(x) for x in leftovers], dtype=np.float32))
        q = np.asarray([float(_get(x, "quality") or 0) for x in leftovers], dtype=np.float32)
        centers = [_norm(np.asarray(_emb(x), dtype=np.float32)) for x in chosen] + man_centers(None)
        idx = _k_center(emb, q, centers, free_after)
        chosen += [leftovers[i] for i in idx]

    out = keep + [int(_get(x, "id")) for x in chosen]
    free_after = free - len(chosen)
    if free_after > 0:
        tail = sorted(poor, key=lambda x: float(_get(x, "quality") or 0), reverse=True)
        tail += sorted(weak_tail, key=lambda x: float(_get(x, "quality") or 0), reverse=True)
        out += [int(_get(x, "id")) for x in tail[:free_after]]
    return out


def mean_embedding(vectors: Iterable[Any]) -> Optional[np.ndarray]:
    """L2-normiertes Mittel L2-normierter Vektoren (Dimension des ersten); None wenn leer/ungültig."""
    vs = [np.asarray(v, dtype=np.float32) for v in vectors if v is not None and len(v)]
    if not vs:
        return None
    d = vs[0].shape[0]
    vs = [v for v in vs if v.shape[0] == d and np.all(np.isfinite(v)) and float(np.linalg.norm(v)) > 0]
    if not vs:
        return None
    m = _norm(np.stack(vs)).mean(axis=0)
    if float(np.linalg.norm(m)) == 0.0:
        return None
    return _norm(m)
