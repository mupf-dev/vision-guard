"""VisionGuard Backend – FastAPI-Einstiegspunkt."""
import asyncio
import contextlib
import logging
import os

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from . import go2rtc, registry, retention, settings_store, state
from .auth import ws_user
from .config import DEFAULT_LABELS, VERSION, config, secret_key_problem, weak_admin_password
from .db import SessionLocal, init_db
from .events import close_stale_events, processor
from .models import KV, Camera, Rule
from .mqtt_bus import bus
from .routers import auth as auth_router
from .routers import cameras, events, identities, internal, misc
from .routers import rules as rules_router
from .routers import inventory as inventory_router
from .inventory import memory as inventory_memory
from .routers import media as media_router
from .routers import tokens as tokens_router
from .routers import notifications as notifications_router
from .rules import reload as reload_rules
from . import health as camera_health
from . import rule_conditions  # noqa: F401  – registriert die Bedingungs-Auswertungen (A1)
# Welle 2 (K2): Fach-Module mit fertiger Verdrahtung (Inhalt: C1, C3, D2, B3)
from . import embed_client, pet_diary, pose_events, review
from . import review_effects  # D2: Rückfragen-Erzeuger und -Wirkungen
from . import report as daily_report
from .mqtt_bus import register_handler
from .routers import pets as pets_router
from .routers import reports as reports_router
from .routers import review as review_router
# Welle 3 (K3): Fach-Module mit fertiger Verdrahtung (Inhalt: A2, A3+C4, B1)
from . import history as time_machine
from . import sensors as virtual_sensors
from . import vlm
from .routers import ask as ask_router
from .routers import history as history_router
from .routers import sensors as sensors_router
# Welle 4: Person über mehrere Kameras (C2)
from . import reid as presence_reid
from .routers import presence as presence_router
from .sync import load_cameras
from .util import ensure_media_dirs
from .ws import hub

logging.basicConfig(level=os.getenv("VG_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("vg")


def seed_demo() -> None:
    """Beim allerersten Start (leere Kamera-Tabelle) Demo-Kameras und Beispielregeln anlegen."""
    with SessionLocal() as s:
        if s.get(KV, "seeded") is not None:
            return
        if config.demo_cameras and (s.scalar(select(func.count()).select_from(Camera)) or 0) == 0:
            log.info("Lege Demo-Kameras und Beispielregeln an")
            for name, url in (("Demo Eingang", "rtsp://testcam:8554/cam1"), ("Demo Straße", "rtsp://testcam:8554/cam2")):
                s.add(Camera(name=name, url=url, detect_url=None, enabled=True, detect_fps=5,
                             labels=list(DEFAULT_LABELS), min_score=0.5, face_recognition=True,
                             plate_recognition=True, pet_recognition=True, zones=[]))
            if (s.scalar(select(func.count()).select_from(Rule)) or 0) == 0:
                s.add(Rule(name="Person erkannt → Snapshot + MQTT", enabled=True, camera_ids=[], zone_ids=[],
                           labels=["person"], min_score=0.5, identity={"mode": "any", "identity_ids": []},
                           trigger="enter", min_dwell_s=0, schedule=None, cooldown_s=30,
                           actions=[{"type": "snapshot"},
                                    {"type": "mqtt", "topic": "visionguard/alarm/{camera}", "payload": None,
                                     "retain": False}]))
                s.add(Rule(name="Fahrzeug → Clip", enabled=True, camera_ids=[], zone_ids=[],
                           labels=["car", "truck", "bus", "motorcycle"], min_score=0.5,
                           identity={"mode": "any", "identity_ids": []}, trigger="enter", min_dwell_s=0,
                           schedule=None, cooldown_s=30, actions=[{"type": "clip", "pre_s": 5, "post_s": 10}]))
        s.add(KV(key="seeded", value=True))
        s.commit()


def _w2_tick() -> None:
    """W2: Ticks der Fach-Module (jedes drosselt sich selbst); Fehler eines Moduls stoppen die anderen nicht."""
    for name, fn in (("review", review.tick), ("review_effects", review_effects.tick), ("pet_diary", pet_diary.tick),
                     ("report", daily_report.tick),
                     # W3
                     ("vlm", vlm.tick), ("sensors", virtual_sensors.tick), ("history", time_machine.tick),
                     # W4
                     ("reid", presence_reid.tick)):
        try:
            fn()
        except Exception:
            log.exception("Tick %s fehlgeschlagen", name)


async def _ticker() -> None:
    while True:
        await asyncio.sleep(1)
        await asyncio.to_thread(processor.tick)
        await asyncio.to_thread(inventory_memory.tick)
        await asyncio.to_thread(_w2_tick)


async def _retention_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(retention.run_once)
        except Exception:
            log.exception("Aufräumen fehlgeschlagen")
        await asyncio.sleep(3600)


async def _go2rtc_startup_sync() -> None:
    if not await go2rtc.wait_ready(timeout=300):
        log.warning("go2rtc nicht erreichbar – Streams werden später synchronisiert")
    # Wiederholen, bis es klappt (go2rtc kann nach uns starten/neu starten)
    while not await go2rtc.sync(load_cameras()):
        await asyncio.sleep(5)
    log.info("go2rtc-Streams synchronisiert")
    # go2rtc verliert per API angelegte Streams bei einem Neustart → regelmäßig abgleichen
    while True:
        await asyncio.sleep(30)
        await go2rtc.sync(load_cameras())


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    problem = secret_key_problem()
    if problem:
        log.critical("%s – Start abgebrochen. Bitte in .env SECRET_KEY setzen, z. B. mit `openssl rand -hex 32` "
                     "(macht bestehende Sitzungen, API-Keys und Medien-Links ungültig).", problem)
        raise RuntimeError(problem)
    if weak_admin_password():
        log.warning("Schwaches Admin-Passwort (ADMIN_PASSWORD): Anmeldung ist nur aus privaten Netzen möglich. "
                    "Für Zugriff von außen ein starkes Passwort (≥ 8 Zeichen, nicht 'admin') setzen.")
    loop = asyncio.get_running_loop()
    hub.bind(loop)
    processor.loop = loop
    ensure_media_dirs()
    await asyncio.to_thread(init_db)
    await asyncio.to_thread(settings_store.load)
    await asyncio.to_thread(seed_demo)
    await asyncio.to_thread(close_stale_events)
    await asyncio.to_thread(registry.reload)
    await asyncio.to_thread(reload_rules)
    await asyncio.to_thread(inventory_memory.startup)
    await asyncio.to_thread(camera_health.startup)
    await asyncio.to_thread(pet_diary.startup)
    await asyncio.to_thread(virtual_sensors.startup)
    await asyncio.to_thread(presence_reid.startup)     # W4 (C2): Track-Hook der Anwesenheit
    # W2: Worker-Nachrichten an Fach-Module (vg/internal/<kind>/…)
    register_handler("embed_result", embed_client.handle_result, direct=True)
    register_handler("gesture", pose_events.handle_gesture)
    # W3: Sensor-Messwerte (A3), Zeitmaschinen-Vektoren (B1)
    register_handler("sensor", virtual_sensors.handle_worker_values)
    register_handler("history", time_machine.handle_worker_frame)
    bus.start()
    bus.publish_config_changed()
    tasks = [asyncio.create_task(t) for t in (_ticker(), _retention_loop(), _go2rtc_startup_sync())]
    log.info("VisionGuard Backend %s gestartet", VERSION)
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        bus.stop()


app = FastAPI(title="VisionGuard", version=VERSION, lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
app.add_middleware(media_router.SignedUrlsMiddleware)   # ?signed=1 → signierte Medien-URLs
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

for r in (tokens_router.router, media_router.router):
    app.include_router(r)

app.include_router(inventory_router.router)
app.include_router(notifications_router.router)
for r in (review_router.router, review_router.public_router, pets_router.router, reports_router.router):   # W2
    app.include_router(r)

# W3: ask zuerst (POST /api/cameras/{id}/ask mit Scope read, eigener Router ohne router-weites require_user)
for r in (ask_router.router, sensors_router.router, history_router.router):
    app.include_router(r)
app.include_router(presence_router.router)   # W4 (C2)

for r in (auth_router.router, cameras.router, rules_router.router, events.router,
          identities.router, misc.router, internal.router):
    app.include_router(r)


@app.get("/api/health", include_in_schema=False)
def health():
    return {"ok": True, "version": VERSION, "mqtt": state.mqtt_connected}


@app.websocket("/api/ws")
async def websocket(ws: WebSocket):
    if not await asyncio.to_thread(ws_user, ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    initial = []
    for cam in registry.all():
        initial.append({"type": "camera_status", "camera_id": cam["id"],
                        "status": state.status_for(cam["id"], cam["enabled"])})
    await hub.serve(ws, initial)


os.makedirs(config.media_dir, exist_ok=True)
# /media/* liefert routers/media.py geschützt aus (Cookie | Bearer | Signatur), kein StaticFiles-Mount mehr
