"""Identitäten 07 (Paket B): Gesichter je Track sammeln statt 3× sofort – CONTRACT ID.F/ID.G.

Ablauf je Personen-Track (Kamera mit `face_recognition`):

1. **Sammeln** (`dispatch`, aus `Pipeline._post`): über die ganze Track-Dauer gedrosselt Kopf-Ausschnitte an das
   Gesichtsmodell geben – je Track alle `FACE_INTERVAL_S` (0,4 s), sobald `face_track_faces` gute Gesichter da sind
   nur noch alle `FACE_INTERVAL_SLOW_S`; höchstens `FACE_MAX_JOBS_TRACK` je Track; nach den ersten `FACE_FREE_JOBS`
   (3, wie früher) teilen sich alle Tracks global `FACE_MAX_JOBS_PER_S` (Token-Bucket). Ist für die Kamera `face_fullres` wirksam, kommt der Ausschnitt aus dem zeitgleichen Bild des
   Hauptstroms in voller Auflösung (`fullres.py`), sonst aus dem Analysebild. Volle Warteschlange/Budget → verwerfen.
2. **Beste K behalten** (`on_face`, Gesichtsmodell-Thread): Kandidaten nach Qualität (`face_quality.quality`), die
   besten `face_track_faces` (Setting, 1..10) bleiben im RAM (Merkmal + kleiner Ausschnitt).
3. **Vorläufig** (`phase:"provisional"`): beim ersten brauchbaren Gesicht (Qualität ≥ `FACE_PROVISIONAL_MIN_Q`), ein
   zweites Mal nur, wenn noch keine Identität zugeordnet ist und ein deutlich besseres Gesicht (≥ 1,5×) kommt.
   Das Backend ordnet dabei nur bestehenden Identitäten zu (legt nie neue an, lernt nicht).
4. **Endgültig** (`phase:"final"`): nach `FACE_FINAL_AFTER_S` (30 s) Track-Dauer und am Track-Ende erneut (nur wenn
   seitdem neue Gesichter dazukamen). Merkmal = L2-normiertes Mittel der besten K (Ausreißer mit Kosinus
   < `FACE_TRACK_OUTLIER` zum Mittel fliegen raus – z. B. ein zweites Gesicht im Ausschnitt), die Einzelbilder gehen
   als `samples` mit (Galerie-Kandidaten). Das Track-Ende wartet darauf wie bisher höchstens 6 s (`pending`).
   Antwort `none`/`ambiguous` entfernt eine zuvor vorläufig gesetzte Gesichts-Identität vom Track (nicht eine
   per ReID übernommene).
5. Kennzahlen (letzte 60 s) für `vg/internal/system` → `perf.face`.

Fehler (Modell, HTTP) werden geloggt und beenden nichts; ein Track ohne Gesicht bleibt ohne Identität.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import settings
from .util import crop_norm, new_media_path, write_jpeg

log = logging.getLogger("vg.face_track")


@dataclass
class Candidate:
    quality: float
    embedding: np.ndarray             # L2-normiert
    image: np.ndarray                 # Gesichtsausschnitt (BGR, ≤ FACE_SAMPLE_MAX_PX)
    meta: dict                        # face_size, yaw, pitch, sharpness, day_night, source_res
    ts: float
    crop_path: str | None = None


@dataclass
class TrackFaces:
    lock: threading.Lock = field(default_factory=threading.Lock)
    first_seen: float = 0.0
    last_job: float = 0.0
    jobs: int = 0
    cands: list[Candidate] = field(default_factory=list)
    version: int = 0                  # erhöht bei jeder Änderung von cands
    prov_sent: int = 0
    prov_quality: float = 0.0
    final_seq: int = 0                # laufende Nummer der final-Anfragen
    final_version: int = -1           # cands-Version der letzten final-Anfrage
    final_applied_seq: int = 0
    has_identity: bool = False        # Gesichts-Identität (vorläufig oder endgültig) gesetzt
    ended: bool = False


def _state(tr) -> TrackFaces:
    st = tr.ids.get("face")
    if st is None:
        st = tr.ids["face"] = TrackFaces(first_seen=tr.first_seen)
    return st


class _Rate:
    def __init__(self):
        self.ev: deque[float] = deque(maxlen=20000)

    def add(self, n: int = 1):
        now = time.time()
        for _ in range(n):
            self.ev.append(now)

    def per_min(self) -> int:
        now = time.time()
        return sum(1 for t in list(self.ev) if now - t <= 60)


class FaceCollector:
    def __init__(self, identifier):
        self.idf = identifier            # identify.Identifier: face model, http pool, identify_fn, callbacks
        self.k = 5                       # face_track_faces (Settings)
        self.tokens = float(settings.FACE_MAX_JOBS_PER_S)
        self.t_tokens = time.time()
        self.tok_lock = threading.Lock()
        self.r_faces = _Rate()
        self.r_prov = _Rate()
        self.r_final = _Rate()
        self.r_dropped = _Rate()
        self.fullres = None              # fullres.FullResManager (von der Pipeline gesetzt)
        self.counts: Counter = Counter()
        # Prüfung 07: Ausbeute je Bildquelle (main = Vollbild-Ausschnitt, detect = Analysebild) für perf.face
        self.r_src: dict[str, tuple[_Rate, _Rate]] = {"main": (_Rate(), _Rate()), "detect": (_Rate(), _Rate())}

    # ------------------------------------------------------------------ Einstellungen
    def set_settings(self, s: dict | None):
        if not s:
            return
        try:
            v = s.get("face_track_faces")
            if v is not None:
                self.k = max(1, min(10, int(v)))
        except (TypeError, ValueError):
            pass

    # ------------------------------------------------------------------ 1. Sammeln
    def _take_token(self) -> bool:
        with self.tok_lock:
            now = time.time()
            rate = max(0.1, settings.FACE_MAX_JOBS_PER_S)
            self.tokens = min(rate, self.tokens + (now - self.t_tokens) * rate)
            self.t_tokens = now
            if self.tokens < 1.0:
                return False
            self.tokens -= 1.0
            return True

    def dispatch(self, c, f, tr) -> None:
        """Aus Pipeline._post (Post-Thread der Kamera) für einen getroffenen Personen-Track."""
        from .identify import Job
        st = _state(tr)
        now = f.ts
        if st.jobs >= settings.FACE_MAX_JOBS_TRACK:
            return
        with st.lock:
            good = sum(1 for x in st.cands if x.quality >= settings.FACE_GOOD_Q)
        interval = settings.FACE_INTERVAL_SLOW_S if good >= self.k else settings.FACE_INTERVAL_S
        if now - st.last_job < interval:
            return
        if tr.score < float(c.cfg.get("min_score") or 0.5) * 0.8:
            return
        reader = self.fullres.get(c.id) if (self.fullres is not None and c.cfg.get("face_fullres")) else None
        ff = None
        if reader is not None:
            reader.demand(f.image.shape[1])
            if reader.state == "running":
                ff = reader.frame_near(f.ts)
                # Vollbild-Leser läuft mit wenigen Bildern/s: auf ein zeitgleiches Vollbild warten (nächster Frame),
                # nur wenn es zu lange dauert, aus dem Analysebild schneiden
                if ff is None and now - st.last_job < settings.FACE_FULLRES_WAIT_S:
                    return
        # Kopf-Ausschnitt (oberer Teil der Personenbox); erst schneiden (billig), dann Budget nehmen
        src_img = ff.image if ff is not None else f.image
        t0 = time.perf_counter()
        crop = crop_norm(src_img, tr.bbox, margin=0.05, region=(0.0, 0.45))
        px_scale, src = 1.0, ("main" if ff is not None else "detect")
        if crop is None or crop.shape[0] < 40 or crop.shape[1] < 24:
            return
        # die ersten FACE_FREE_JOBS je Track (wie früher 3 Versuche gleich zu Beginn) laufen ohne globales Budget,
        # damit jeder Track früh ein Gesicht bekommt; danach teilen sich alle Tracks FACE_MAX_JOBS_PER_S
        if st.jobs >= settings.FACE_FREE_JOBS and not self._take_token():
            st.last_job = now            # nicht in jedem Frame erneut versuchen
            self.r_dropped.add()
            self.counts["drop_budget"] += 1
            return
        if ff is not None:
            m = max(crop.shape[:2])
            if m > settings.FACE_CROP_MAX_PX:
                sc = settings.FACE_CROP_MAX_PX / m
                crop = cv2.resize(crop, (max(1, int(crop.shape[1] * sc)), max(1, int(crop.shape[0] * sc))),
                                  interpolation=cv2.INTER_AREA)
                px_scale = 1.0 / sc
            self.fullres.note_crop((time.perf_counter() - t0) * 1000)
        st.last_job = now
        st.jobs += 1
        job = Job(c.id, tr.tid, crop, now, {"track_state": tr.ids, "faces": st, "px_scale": px_scale,
                                            "source_res": src, "track_first_seen": tr.first_seen})
        if self.idf.submit("face", job):
            self.counts[f"jobs_{src}"] += 1
            self.r_src[src][0].add()
        else:
            self.r_dropped.add()
            self.counts["drop_queue"] += 1

    # ------------------------------------------------------------------ 2.–4. Ergebnisse
    def on_face(self, job, res: dict) -> None:
        """Gesichtsmodell-Thread: ein Gesicht wurde gefunden und eingebettet."""
        st: TrackFaces = job.extra["faces"]
        self.r_faces.add()
        self.counts[f"faces_{job.extra.get('source_res') or 'detect'}"] += 1
        self.r_src.get(job.extra.get("source_res") or "detect", self.r_src["detect"])[1].add()
        meta = {"face_size": int(round(res["size"])), "yaw": res.get("yaw"), "pitch": res.get("pitch"),
                "sharpness": res.get("sharpness"), "day_night": res.get("day_night"),
                "source_res": job.extra.get("source_res") or "detect"}
        cand = Candidate(float(res["quality"]), res["embedding"], res["face_crop"], meta, job.ts)
        send_prov = send_final = False
        with st.lock:
            if st.ended:
                return            # Track-Ende ist schon abgewickelt (späte Ergebnisse verwerfen)
            st.cands.append(cand)
            st.cands.sort(key=lambda x: x.quality, reverse=True)
            if len(st.cands) > self.k:
                del st.cands[self.k:]
            if any(x is cand for x in st.cands):
                st.version += 1
            q = cand.quality
            if q >= settings.FACE_PROVISIONAL_MIN_Q and st.final_seq == 0 and (
                    st.prov_sent == 0 or (st.prov_sent < 2 and not st.has_identity and q >= 1.5 * st.prov_quality)):
                st.prov_sent += 1
                st.prov_quality = q
                send_prov = True
            if st.final_seq == 0 and job.ts - st.first_seen >= settings.FACE_FINAL_AFTER_S:
                send_final = True
        if send_prov:
            self._provisional(job, cand)
        if send_final:
            self.final(job.camera_id, job.track_id, job.extra["track_state"])

    def _provisional(self, job, cand: Candidate) -> None:
        try:
            path = new_media_path("crops", "jpg", cand.ts)
            write_jpeg(path, cand.image)
            cand.crop_path = path
        except Exception as e:  # noqa: BLE001
            log.warning("face crop write failed: %s", e)
            path = None
        body = {"kind": "face", "phase": "provisional", "camera_id": job.camera_id, "track_id": job.track_id,
                "embedding": [round(float(v), 6) for v in cand.embedding], "plate_text": None,
                "crop_path": path, "quality": cand.quality, **cand.meta}
        self.r_prov.add()
        self.counts["provisional"] += 1
        st: TrackFaces = job.extra["faces"]

        def done(r: dict | None):
            if not r:
                return
            with st.lock:
                if st.final_applied_seq > 0:
                    return                       # endgültige Zuordnung hat Vorrang
            if r.get("identity_id") is not None:
                with st.lock:
                    st.has_identity = True
                self.idf.on_identity(job.camera_id, job.track_id, self._ident(r, "provisional"), None)
        self.idf.call_async(body, job.camera_id, job.track_id, done)

    def final(self, camera_id: int, track_id: int, ids: dict, at_end: bool = False) -> bool:
        """Endgültige Zuordnung senden. True, wenn eine Anfrage unterwegs ist (Track-Ende wartet darauf)."""
        st: TrackFaces | None = ids.get("face")
        if st is None:
            return False
        with st.lock:
            if at_end:
                st.ended = True
            if not st.cands or st.final_version == st.version:
                return False
            cands = list(st.cands)
            st.final_seq += 1
            seq = st.final_seq
            st.final_version = st.version
        # Anfrage (inkl. JPEG-Schreiben der Einzelbilder) erst im HTTP-Thread bauen – final() läuft beim Track-Ende
        # unter dem Lock des Trackers
        def body():
            try:
                return self._final_body(camera_id, track_id, cands)
            except Exception:  # noqa: BLE001
                log.exception("building final face request failed")
                return None
        self.r_final.add()
        self.counts["final"] += 1

        def done(r: dict | None):
            if not r:
                return
            with st.lock:
                if seq < st.final_applied_seq:
                    return                       # ältere Antwort nach neuerer
                st.final_applied_seq = seq
            if r.get("identity_id") is not None:
                with st.lock:
                    st.has_identity = True
                self.idf.on_identity(camera_id, track_id, self._ident(r, "final"), None)
            elif r.get("decision") in ("none", "ambiguous"):
                with st.lock:
                    had = st.has_identity
                    st.has_identity = False
                if had:
                    self.idf.on_identity(camera_id, track_id, None, None, clear=True)
        self.idf.call_async(body, camera_id, track_id, done)
        return True

    def _final_body(self, camera_id: int, track_id: int, cands: list[Candidate]) -> dict | None:
        embs = np.stack([c.embedding for c in cands]).astype(np.float32)
        mean = embs.mean(axis=0)
        n = float(np.linalg.norm(mean))
        if n <= 0:
            return None
        mean /= n
        if len(cands) >= 3:
            sims = embs @ mean
            keep = [i for i, s in enumerate(sims) if s >= settings.FACE_TRACK_OUTLIER]
            if keep and len(keep) < len(cands):
                self.counts["outliers"] += len(cands) - len(keep)
                cands = [cands[i] for i in keep]
                mean = embs[keep].mean(axis=0)
                mean /= max(1e-9, float(np.linalg.norm(mean)))
        samples = []
        for c in cands:
            if c.crop_path is None:
                try:
                    c.crop_path = new_media_path("crops", "jpg", c.ts)
                    write_jpeg(c.crop_path, c.image)
                except Exception as e:  # noqa: BLE001
                    log.warning("face crop write failed: %s", e)
                    c.crop_path = None
            samples.append({"embedding": [round(float(v), 6) for v in c.embedding], "crop_path": c.crop_path,
                            "quality": c.quality, **c.meta})
        best = cands[0]
        dn = Counter(c.meta.get("day_night") for c in cands if c.meta.get("day_night")).most_common(1)
        return {"kind": "face", "phase": "final", "camera_id": camera_id, "track_id": track_id,
                "embedding": [round(float(v), 6) for v in mean], "plate_text": None,
                "crop_path": best.crop_path, "quality": best.quality,
                "face_size": max(int(c.meta.get("face_size") or 0) for c in cands),
                "yaw": best.meta.get("yaw"), "pitch": best.meta.get("pitch"),
                "sharpness": best.meta.get("sharpness"),
                "day_night": dn[0][0] if dn else best.meta.get("day_night"),
                "source_res": best.meta.get("source_res"), "n_faces": len(cands), "samples": samples}

    def on_track_end(self, camera_id: int, tr) -> bool:
        """Vom Tracker kurz vor dem Track-Ende (unter dessen Lock): letzte endgültige Zuordnung anstoßen."""
        if tr.label != "person":
            return False
        try:
            return self.final(camera_id, tr.tid, tr.ids, at_end=True)
        except Exception:  # noqa: BLE001
            log.exception("final face identification failed")
            return False

    @staticmethod
    def _ident(r: dict, phase: str) -> dict:
        return {"id": r["identity_id"], "display_name": r.get("display_name"), "name": r.get("name"),
                "kind": "face", "phase": phase}

    # ------------------------------------------------------------------ Kennzahlen
    def perf(self) -> dict:
        n, ms = self.fullres.perf() if self.fullres is not None else (0, 0.0)
        return {"faces_per_min": self.r_faces.per_min(), "provisional_per_min": self.r_prov.per_min(),
                "final_per_min": self.r_final.per_min(), "fullres_per_min": n, "fullres_ms": ms,
                "dropped": self.r_dropped.per_min(),
                "by_source": {k: {"jobs_per_min": j.per_min(), "faces_per_min": f.per_min()}
                              for k, (j, f) in self.r_src.items()}}
