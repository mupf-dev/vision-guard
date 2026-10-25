"""Frag die Kamera (W3, A2). Vertrag: CONTRACT.md „Nachträge: Welle 3“, W3.E (+ W3.C GPU-Lease).

Ablauf `ask(...)` (blockierend, der Router ruft es im Threadpool):
1. Aktuelles Bild des Hauptstreams über go2rtc (`frame.jpeg?src=cam_N`) – 502, wenn keins kommt.
2. Lokal: Dienst `vlm` (Compose-Profil `vlm`, `config.vlm_url`) `POST /infer` – der Dienst holt sich selbst die
   GPU-Lease vom Worker (YOLOE pausiert) und schneidet `region` aus.
3. Cloud (`cloud_vlm.py`, über `llm_client`): bei `engine:"cloud"` oder bei `auto`, wenn lokal nicht verfügbar /
   beschäftigt ist – **nur** mit `cameras.vlm_cloud_allowed` und gesetztem `vlm_cloud_model`.
4. Standbild unter `ask/YYYY-MM-DD/cam{N}_{unix}.jpg` (≤ 1920 px breit), Eintrag in `ask_log`.

Ja/Nein-Fragen (beginnen mit is/are/do/can/… bzw. ist/sind/gibt/…) gehen im Modus `yesno` an das Modell
(„Answer with yes or no first …“), `yes_no` wird heuristisch aus dem ersten Wort gelesen (`unknown` erlaubt).
Fehlgeschlagene Fragen landen mit `error` in `ask_log` (ohne Bild) und erscheinen nicht in `GET /ask`.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import delete, select

from . import go2rtc, settings_store
from .config import config
from .db import SessionLocal
from .models import AskLog, Camera
from .util import local_date_str, media_path, safe_remove

log = logging.getLogger("vg.vlm")

SNAPSHOT_MAX_W = 1920
INFER_TIMEOUT_S = 45.0          # Laden (bis ~10 s, Qwen) + Lease (≤ 10 s) + Antwort (≤ 20 s)
HEALTH_EVERY_S = 10.0
FRAME_CACHE = "3s"

_YESNO_EN = ("is", "are", "am", "was", "were", "do", "does", "did", "can", "could", "has", "have", "had", "will",
             "would", "should", "shall", "may", "might", "must", "isn't", "aren't", "doesn't", "don't")
_YESNO_DE = ("ist", "sind", "war", "waren", "gibt", "gab", "hat", "haben", "kann", "können", "liegt", "liegen",
             "steht", "stehen", "sitzt", "sitzen", "brennt", "leuchtet", "läuft", "wird", "werden", "darf", "muss",
             "sieht", "befindet", "befinden", "fehlt", "fehlen", "trägt", "hängt", "hängen")


class AskError(Exception):
    """Fehler mit HTTP-Status und deutscher Meldung (Router übersetzt in HTTPException)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ------------------------------------------------------------------ Hilfen (auch für cloud_vlm / A3)

def is_yes_no_question(question: str) -> bool:
    w = (question or "").strip().lower().split(maxsplit=1)
    return bool(w) and (w[0] in _YESNO_EN or w[0] in _YESNO_DE)


def parse_yes_no(answer: str) -> str:
    a = (answer or "").strip().lower().lstrip("*\"'`- ")
    first = re.sub(r"[^a-zäöü]", "", a.split(maxsplit=1)[0]) if a else ""
    if first in ("yes", "yeah", "yep", "ja", "jawohl", "true", "correct"):
        return "yes"
    if first in ("no", "nope", "nein", "false"):
        return "no"
    return "unknown"


def crop_jpeg(data: bytes, region: Optional[list[float]], max_w: Optional[int] = None) -> bytes:
    """Ausschnitt (normiert x1,y1,x2,y2) und/oder Verkleinerung; bei Fehlern das Original."""
    if not region and not max_w:
        return data
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            changed = False
            if region:
                x1, y1, x2, y2 = (max(0.0, min(1.0, float(v))) for v in region)
                if x2 - x1 >= 0.01 and y2 - y1 >= 0.01:
                    im = im.crop((round(x1 * im.width), round(y1 * im.height),
                                  round(x2 * im.width), round(y2 * im.height)))
                    changed = True
            if max_w and im.width > max_w:
                im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
                changed = True
            if not changed:
                return data
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85)
            return out.getvalue()
    except Exception as e:  # noqa: BLE001
        log.debug("Bild nicht bearbeitbar: %s", e)
        return data


def grab_frame(camera_id: int, timeout: float = 15.0) -> bytes:
    """JPEG des Hauptstreams (volle Auflösung) über go2rtc.

    `cache=3s`: go2rtc liefert innerhalb von 3 s dasselbe Bild ohne erneutes Warten auf einen Keyframe (gemessen
    `cam_3`: 0,6–2,0 s → 0 s) – mehrere Fragen kurz hintereinander (MCP-Agent, Sensoren) beziehen sich so auf
    dasselbe Bild."""
    try:
        r = httpx.get(f"{config.go2rtc_url}/api/frame.jpeg",
                      params={"src": go2rtc.stream_name(camera_id), "cache": FRAME_CACHE}, timeout=timeout)
    except httpx.HTTPError as e:
        log.info("Standbild Kamera %s nicht verfügbar: %s", camera_id, type(e).__name__)
        raise AskError(502, "Kamerabild nicht verfügbar") from None
    if r.status_code != 200 or not r.content.startswith(b"\xff\xd8"):
        log.info("Standbild Kamera %s nicht verfügbar: HTTP %s", camera_id, r.status_code)
        raise AskError(502, "Kamerabild nicht verfügbar")
    return r.content


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
        if isinstance(d, str):
            return d
    except Exception:  # noqa: BLE001
        pass
    return f"HTTP {r.status_code}"


def ask_local(image: bytes, question: str, region: Optional[list[float]], mode: str) -> dict[str, Any]:
    """Dienst `vlm` → {answer, yes_no, ms, model}. Wirft AskError(503|429)."""
    try:
        r = httpx.post(f"{config.vlm_url}/infer", timeout=INFER_TIMEOUT_S,
                       json={"image_b64": base64.b64encode(image).decode(), "question": question,
                             "region": region, "mode": mode})
    except httpx.ConnectError:
        _set_health({"state": "off"})
        raise AskError(503, "Bildmodell nicht verfügbar (Dienst vlm läuft nicht – Compose-Profil vlm)") from None
    except httpx.TimeoutException:
        raise AskError(503, "Bildmodell nicht verfügbar (Zeitlimit)") from None
    except httpx.HTTPError as e:
        raise AskError(503, f"Bildmodell nicht verfügbar ({type(e).__name__})") from None
    if r.status_code == 429:
        raise AskError(429, "Bildmodell beschäftigt")
    if r.status_code == 503:
        d = _detail(r)
        raise AskError(503, d if d.startswith("Bildmodell") or d.startswith("Lokales") else
                       f"Bildmodell nicht verfügbar ({d})")
    if r.status_code >= 400:
        raise AskError(503, f"Bildmodell nicht verfügbar ({_detail(r)})")
    d = r.json()
    return {"answer": str(d.get("answer") or "").strip(), "yes_no": d.get("yes_no") or "unknown",
            "model": str(d.get("model") or ""), "ms": float(d.get("ms") or 0)}


# ------------------------------------------------------------------ Status des Dienstes (für /system)

_health: dict[str, Any] = {"state": "off"}
_health_ts = 0.0
_health_lock = threading.Lock()


def _set_health(h: dict[str, Any]) -> None:
    global _health, _health_ts
    with _health_lock:
        _health = h
        _health_ts = time.time()


def health() -> dict[str, Any]:
    with _health_lock:
        return dict(_health)


def status() -> str:
    """`off|loading|ready|error: …` für `/system.worker.models.vlm` (aus `vlm /health`, alle 10 s)."""
    st = settings_store.get()
    if not st.get("vlm_enabled", True):
        return "off"
    return str(health().get("state") or "off")


def tick() -> None:
    """Jede Sekunde vom Backend-Ticker – `vlm /health` höchstens alle 10 s."""
    if time.time() - _health_ts < HEALTH_EVERY_S:
        return
    try:
        r = httpx.get(f"{config.vlm_url}/health", timeout=2.0)
        _set_health(r.json() if r.status_code == 200 else {"state": f"error: HTTP {r.status_code}"})
    except (httpx.HTTPError, ValueError):
        _set_health({"state": "off", "reachable": False})


# ------------------------------------------------------------------ Frage

def _camera(camera_id: int) -> dict[str, Any]:
    with SessionLocal() as s:
        c = s.get(Camera, camera_id)
        if c is None:
            raise AskError(404, "Kamera nicht gefunden")
        return {"id": c.id, "name": c.name, "enabled": bool(c.enabled),
                "vlm_cloud_allowed": bool(getattr(c, "vlm_cloud_allowed", False))}


def _save_snapshot(camera_id: int, data: bytes, ts: datetime) -> Optional[str]:
    rel = f"ask/{local_date_str(ts)}/cam{camera_id}_{int(ts.timestamp() * 1000)}.jpg"
    try:
        full = media_path(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(crop_jpeg(data, None, SNAPSHOT_MAX_W))
        return rel
    except OSError as e:
        log.warning("Standbild für Frage nicht gespeichert: %s", e)
        return None


def ask_json(a: AskLog, camera_name: Optional[str] = None) -> dict[str, Any]:
    return {"id": a.id, "camera_id": a.camera_id, "camera_name": camera_name or "", "question": a.question,
            "region": a.region, "answer": a.answer, "yes_no": a.yes_no or "unknown", "engine": a.engine,
            "model": a.model, "ms": round(float(a.ms or 0)), "snapshot_url": f"/media/{a.snapshot_path}"
            if a.snapshot_path else None, "ts": a.ts.isoformat() if a.ts else None, "source": a.source}


def _log(camera: dict, question: str, region, source: str, token_name, *, result: Optional[dict] = None,
         error: Optional[str] = None, snapshot_path: Optional[str] = None, ts: datetime) -> AskLog:
    with SessionLocal() as s:
        a = AskLog(ts=ts, camera_id=camera["id"], question=question, region=region, source=source,
                   token_name=token_name, snapshot_path=snapshot_path, error=error)
        if result:
            a.answer = result["answer"]
            a.yes_no = result["yes_no"]
            a.engine = result["engine"]
            a.model = result["model"][:100]
            a.ms = float(result["ms"])
        s.add(a)
        s.commit()
        s.refresh(a)
        s.expunge(a)
        return a


def ask(camera_id: int, question: str, *, region: Optional[list[float]] = None, engine: str = "auto",
        source: str = "ui", token_name: Optional[str] = None, image: Optional[bytes] = None,
        record: bool = True) -> dict[str, Any]:
    """Frage an das aktuelle Bild der Kamera → AskResult (W3.E). Blockierend.

    Zusätzlich (für A3-Sensoren der Art `vlm`): `image` = eigenes JPEG statt go2rtc-Bild, `record=False` = nichts
    speichern (kein Standbild, kein ask_log-Eintrag; `id`/`snapshot_url` sind dann `None`)."""
    question = (question or "").strip()[:300]
    cam = _camera(camera_id)
    if not cam["enabled"]:
        raise AskError(409, "Kamera ist deaktiviert")
    st = settings_store.get()
    from . import cloud_vlm

    cloud_ready = cloud_vlm.available(cam)
    if engine == "cloud":
        if not cam["vlm_cloud_allowed"]:
            raise AskError(403, "Cloud für diese Kamera nicht freigegeben")
        if not cloud_ready:
            raise AskError(503, "Bildmodell nicht verfügbar (Cloud-Modell nicht eingerichtet)")
    if engine == "local" and not st.get("vlm_enabled", True):
        raise AskError(503, "Lokales Bildmodell ist ausgeschaltet (Einstellungen → Frag die Kamera)")
    ts = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    try:
        img = image if image is not None else grab_frame(camera_id)
        mode = "yesno" if is_yes_no_question(question) else "query"
        result: Optional[dict[str, Any]] = None
        local_err: Optional[AskError] = None
        if engine in ("auto", "local") and st.get("vlm_enabled", True):
            try:
                result = {**ask_local(img, question, region, mode), "engine": "local"}
            except AskError as e:
                if engine == "local" or not cloud_ready:
                    raise
                local_err = e
                log.info("Frag die Kamera: lokal nicht möglich (%s) – Cloud-Fallback", e.message)
        if result is None:
            if not cloud_ready:
                raise local_err or AskError(503, "Bildmodell nicht verfügbar (lokales Modell aus, keine Cloud)")
            result = {**cloud_vlm.ask(img, question, region, mode), "engine": "cloud"}
        result["ms"] = round((time.perf_counter() - t0) * 1000)
    except AskError as e:
        if record:
            try:
                _log(cam, question, region, source, token_name, error=f"{e.status}: {e.message}"[:500], ts=ts)
            except Exception:  # noqa: BLE001
                log.exception("ask_log nicht geschrieben")
        raise
    if not record:
        return {"id": None, "camera_id": camera_id, "camera_name": cam["name"], "question": question,
                "region": region, **result, "snapshot_url": None, "ts": ts.isoformat(), "source": source}
    snap = _save_snapshot(camera_id, img, ts)
    a = _log(cam, question, region, source, token_name, result=result, snapshot_path=snap, ts=ts)
    log.info("Frag die Kamera %s (%s, %s): %s ms, %s", camera_id, result["engine"], result["model"], result["ms"],
             result["yes_no"])
    return ask_json(a, cam["name"])


# ------------------------------------------------------------------ Aufbewahrung

def retention(now: datetime) -> int:
    """Stündlich: ask_log + Bilder älter als `retention_days_ask` löschen; alte Tagesordner unter ask/."""
    days = int(settings_store.get().get("retention_days_ask") or 7)
    cutoff = now - timedelta(days=days)
    with SessionLocal() as s:
        old = s.execute(select(AskLog.id, AskLog.snapshot_path).where(AskLog.ts < cutoff)).all()
        for _, p in old:
            safe_remove(p)
        if old:
            s.execute(delete(AskLog).where(AskLog.id.in_([r[0] for r in old])))
            s.commit()
    # verwaiste Tagesordner (z. B. nach manuellem Löschen der Einträge)
    try:
        base = media_path("ask")
        limit = local_date_str(cutoff)
        for d in os.listdir(base):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and d < limit:
                shutil.rmtree(os.path.join(base, d), ignore_errors=True)
    except (OSError, ValueError):
        pass
    if old:
        log.info("Aufräumen: %d Fragen an Kameras entfernt", len(old))
    return len(old)
