"""VisionGuard Backend – FastAPI-Einstiegspunkt."""
import asyncio
import contextlib
import logging
import os

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from . import go2rtc, registry, retention, settings_store, state
from .auth import ws_user
from .config import DEFAULT_LABELS, VERSION, config
from .db import SessionLocal, init_db
from .events import close_stale_events, processor
from .models import KV, Camera, Rule
from .mqtt_bus import bus
from .routers import auth as auth_router
from .routers import cameras, events, identities, internal, misc
from .routers import rules as rules_router
from .rules import reload as reload_rules
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


async def _ticker() -> None:
    while True:
        await asyncio.sleep(1)
        await asyncio.to_thread(processor.tick)


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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

for r in (auth_router.router, cameras.router, rules_router.router, events.router,
          identities.router, misc.router, internal.router):
    app.include_router(r)


@app.get("/api/health", include_in_schema=False)
def health():
    return {"ok": True, "version": VERSION, "mqtt": state.mqtt_connected}


@app.websocket("/api/ws")
async def websocket(ws: WebSocket):
    if not ws_user(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    initial = []
    for cam in registry.all():
        initial.append({"type": "camera_status", "camera_id": cam["id"],
                        "status": state.status_for(cam["id"], cam["enabled"])})
    await hub.serve(ws, initial)


os.makedirs(config.media_dir, exist_ok=True)
app.mount("/media", StaticFiles(directory=config.media_dir, check_dir=False), name="media")
