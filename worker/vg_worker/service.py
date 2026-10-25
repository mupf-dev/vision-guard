"""Worker main service: backend config, MQTT bus, status/system publishing."""
from __future__ import annotations

import json
import logging
import socket
import re
import signal
import threading
import time

import paho.mqtt.client as mqtt
import requests

from . import settings
from .gpu import gpu_info
from .pipeline import Pipeline
from .util import with_worker_id

log = logging.getLogger("vg.service")


class Backend:
    def __init__(self):
        self.s = requests.Session()

    def cameras(self) -> list[dict]:
        r = self.s.get(f"{settings.BACKEND_URL}/api/internal/cameras", timeout=10)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "items" in data:
            data = data["items"]
        return list(data)

    def settings(self) -> dict:
        r = self.s.get(f"{settings.BACKEND_URL}/api/internal/settings", timeout=10)
        r.raise_for_status()
        return r.json()

    def reid(self, body: dict) -> dict | None:
        """W4 (C2): Embedding → presence_key (Backend-Galerie nur im RAM)."""
        r = self.s.post(f"{settings.BACKEND_URL}/api/internal/reid", json=with_worker_id(body), timeout=10)
        r.raise_for_status()
        return r.json()

    def identify(self, body: dict) -> dict | None:
        r = self.s.post(f"{settings.BACKEND_URL}/api/internal/identify", json=with_worker_id(body), timeout=15)
        r.raise_for_status()
        return r.json()


class Service:
    def __init__(self):
        self.backend = Backend()
        self.stop_ev = threading.Event()
        self.reload_ev = threading.Event()
        self.mqtt_connected = False
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"vg-worker-{settings.WORKER_ID}-{int(time.time())}",
                                  clean_session=True)
        self.client.reconnect_delay_set(1, 30)
        self.client.max_queued_messages_set(2000)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.pipeline = Pipeline(self.publish, self.backend.identify, self.backend.reid)

    # ------------------------------------------------------------------ MQTT
    def publish(self, topic: str, payload: dict, qos: int = 0, retain: bool = False):
        if not self.mqtt_connected and qos == 0:
            return      # drop live data while disconnected; QoS1 messages are queued by paho
        try:
            # zentrale Stelle: JEDE Worker-Nachricht trägt worker_id (detections, track, status, system,
            # cmd_result, inventory) – D3-Vorbereitung, das Backend wertet es bisher nur für system aus
            self.client.publish(topic, json.dumps(with_worker_id(payload), separators=(",", ":")), qos=qos,
                                retain=retain)
        except Exception as e:  # noqa: BLE001
            log.debug("publish %s failed: %s", topic, e)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            log.warning("MQTT connect failed: %s", reason_code)
            return
        self.mqtt_connected = True
        log.info("MQTT connected to %s:%s", settings.MQTT_HOST, settings.MQTT_PORT)
        # W2 (K2): Nagle aus – paho setzt kein TCP_NODELAY; bei laufendem Datenstrom verzögerte das kleine
        # Antworten (vg/internal/embed_result) um ~40 ms (Delayed ACK)
        try:
            sock = client.socket()
            if sock is not None:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:  # noqa: BLE001
            pass
        client.subscribe([("vg/internal/config", 1), ("vg/internal/cmd/+", 1), ("vg/internal/embed", 1),
                          # W3 (K3): GPU-Lease für den vlm-Dienst
                          ("vg/internal/gpu/request", 1), ("vg/internal/gpu/release", 1)])
        if not getattr(self, "_lease_announced", False):
            self._lease_announced = True
            self.pipeline.lease.announce_restart()
        self.reload_ev.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.mqtt_connected = False
        log.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload or b"{}") if msg.payload else {}
        except ValueError:
            payload = {}
        if msg.topic == "vg/internal/config":
            log.info("config change notification received")
            self.reload_ev.set()
        elif msg.topic == "vg/internal/embed":   # W2: Embedding-Anfragen (geteiltes DINOv2)
            if not msg.retain:
                self.pipeline.embed.submit(payload)
        elif msg.topic.startswith("vg/internal/gpu/"):   # W3: GPU-Lease (request/release)
            self.pipeline.lease.on_message(msg.topic, payload, retained=bool(msg.retain))
        elif msg.topic.startswith("vg/internal/cmd/"):
            if msg.retain:
                return   # never execute stale retained commands
            try:
                cid = int(msg.topic.rsplit("/", 1)[1])
            except ValueError:
                return
            log.info("command for camera %s: %s", cid, payload)
            threading.Thread(target=self.pipeline.command, args=(cid, payload), daemon=True).start()

    # ------------------------------------------------------------------ main
    def _load_cameras(self) -> bool:
        try:
            cams = self.backend.cameras()
        except Exception as e:  # noqa: BLE001
            log.warning("backend not reachable (%s) – retrying", e)
            return False
        log.info("loaded %d camera(s) from backend", len(cams))
        try:
            self.pipeline.set_settings(self.backend.settings())    # Inventar: confirm_n / miss_m
        except Exception as e:  # noqa: BLE001
            log.warning("settings not loaded (%s) – keeping previous values", e)
        self.pipeline.apply_config(cams)
        return True

    def _status_loop(self):
        while not self.stop_ev.wait(settings.STATUS_INTERVAL_S):
            self.publish_status()

    def publish_status(self):
        for cid, st in self.pipeline.statuses().items():
            self.publish(f"vg/internal/status/{cid}", st, 0, True)
        perf = self.pipeline.perf()
        self.publish("vg/internal/system", {"gpu": gpu_info(), "models": self.pipeline.models_status(),
                                            "perf": perf, "gpu_lease": self.pipeline.gpu_lease(),
                                            "online": True, "ts": round(time.time(), 3)}, 0, True)
        log.debug("perf %s", perf)

    def run(self):
        signal.signal(signal.SIGTERM, lambda *a: self.stop_ev.set())
        signal.signal(signal.SIGINT, lambda *a: self.stop_ev.set())
        self.client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=30)
        self.client.loop_start()
        log.info("loading detector %s ...", settings.DETECT_MODEL)
        self.pipeline.load_detector()
        self.pipeline.start()
        threading.Thread(target=self._status_loop, name="status", daemon=True).start()

        backoff = 2.0
        loaded = False
        last_load = 0.0
        self.reload_ev.set()
        while not self.stop_ev.is_set():
            # periodic refresh as a safety net (config message could be missed while disconnected)
            due = loaded and time.time() - last_load > settings.CONFIG_REFRESH_S
            if self.reload_ev.wait(0.5 if loaded else backoff) or due or not loaded:
                self.reload_ev.clear()
                if self._load_cameras():
                    loaded = True
                    last_load = time.time()
                    backoff = 2.0
                    self.publish_status()
                else:
                    if loaded:
                        last_load = time.time()   # keep running with last config, retry later
                    backoff = min(backoff * 1.5, 15.0)
        log.info("shutting down")
        self.pipeline.stop()
        self.publish_status()
        time.sleep(0.5)
        self.client.loop_stop()
        self.client.disconnect()


def main():
    logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    if not re.match(settings.WORKER_ID_PATTERN, settings.WORKER_ID):
        log.error("VG_WORKER_ID ungültig (%r): erlaubt sind A-Z, a-z, 0-9, _ und -, 1..64 Zeichen",
                  settings.WORKER_ID)
        raise SystemExit(2)
    log.info("worker id: %s", settings.WORKER_ID)
    # Diagnose: `docker kill --signal=USR1 <worker>` schreibt die Stacks aller Threads ins Log (Hänger finden)
    import faulthandler
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    Service().run()
