"""MQTT-Anbindung (paho-mqtt 2.x, eigener Netzwerk-Thread)."""
from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any

import paho.mqtt.client as mqtt

from . import settings_store, state
from .config import config
from .events import processor
from .ws import hub

log = logging.getLogger("vg.mqtt")


class Bus:
    def __init__(self) -> None:
        self.client: mqtt.Client | None = None
        self.prefix = "visionguard"
        # Track-/Ergebnis-Nachrichten werden in einem eigenen Thread verarbeitet (DB-Zugriffe blockieren
        # sonst den MQTT-Netzwerk-Thread und damit den Empfang von Detections).
        self.work: queue.Queue = queue.Queue(maxsize=10000)
        self._worker = threading.Thread(target=self._work_loop, name="vg-track", daemon=True)

    # ------------------------------------------------------------ Verbindung

    def start(self) -> None:
        self.prefix = settings_store.get()["mqtt_prefix"]
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="visionguard-backend", clean_session=True)
        c.will_set(f"{self.prefix}/available", "offline", qos=1, retain=True)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(1, 30)
        self.client = c
        processor.publish = self.publish
        self._worker.start()
        c.connect_async(config.mqtt_host, config.mqtt_port, keepalive=30)
        c.loop_start()

    def stop(self) -> None:
        c = self.client
        if not c:
            return
        try:
            c.publish(f"{self.prefix}/available", "offline", qos=1, retain=True).wait_for_publish(2)
        except Exception:
            pass
        try:
            c.disconnect()
            c.loop_stop()
        except Exception:
            pass

    def set_prefix(self, new_prefix: str) -> None:
        """Prefix geändert: altes 'available' räumen, LWT neu setzen (greift nach Reconnect)."""
        old = self.prefix
        if new_prefix == old or not self.client:
            return
        self.publish(f"{old}/available", "", retain=True)
        self.prefix = new_prefix
        self.client.will_set(f"{new_prefix}/available", "offline", qos=1, retain=True)
        self.publish(f"{new_prefix}/available", "online", retain=True, qos=1)
        try:
            self.client.reconnect()
        except Exception:
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code.is_failure:
            log.warning("MQTT-Verbindung abgelehnt: %s", reason_code)
            return
        state.mqtt_connected = True
        log.info("MQTT verbunden (%s:%s)", config.mqtt_host, config.mqtt_port)
        client.subscribe("vg/internal/#", qos=1)
        client.publish(f"{self.prefix}/available", "online", qos=1, retain=True)
        self.work.put(("reset_counts", None, None))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        state.mqtt_connected = False
        log.warning("MQTT getrennt: %s", reason_code)

    # ------------------------------------------------------------ Senden

    def publish(self, topic: str, payload: Any, retain: bool = False, qos: int = 0) -> None:
        if not self.client:
            return
        if not isinstance(payload, (str, bytes, bytearray)) and payload is not None:
            payload = json.dumps(payload, default=str, separators=(",", ":"))
        self.client.publish(topic, payload, qos=qos, retain=retain)

    def publish_config_changed(self) -> None:
        self.publish("vg/internal/config", "{}", qos=1)

    # ------------------------------------------------------------ Empfang

    def _on_message(self, client, userdata, message) -> None:
        topic = message.topic
        try:
            parts = topic.split("/")
            if len(parts) < 3:
                return
            kind = parts[2]
            if kind in ("cmd", "config"):
                return  # eigene Nachrichten
            if not message.payload:
                return
            msg = json.loads(message.payload)
            if not isinstance(msg, dict):
                return
            if kind == "detections":
                cam_id = int(parts[3]) if len(parts) > 3 else int(msg.get("camera_id"))
                hub.broadcast({
                    "type": "detections", "camera_id": cam_id, "ts": msg.get("ts"),
                    "width": msg.get("width"), "height": msg.get("height"), "objects": msg.get("objects") or [],
                })
            elif kind == "status":
                cam_id = int(parts[3]) if len(parts) > 3 else int(msg.get("camera_id"))
                state.set_camera_status(cam_id, msg)
                from . import registry
                cam = registry.get(cam_id)
                hub.broadcast({"type": "camera_status", "camera_id": cam_id,
                               "status": state.status_for(cam_id, cam["enabled"] if cam else True)})
            elif kind == "system":
                # vg/internal/system (heute) bzw. vg/internal/system/{worker_id} (D3) – je worker_id gespeichert
                state.set_worker_system(msg, parts[3] if len(parts) > 3 and parts[3] else None)
            elif kind == "track":
                cam_id = int(parts[3]) if len(parts) > 3 else int(msg.get("camera_id"))
                self._enqueue(("track", cam_id, msg))
            elif kind == "cmd_result":
                self._enqueue(("cmd_result", None, msg))
            elif kind == "inventory":
                cam_id = int(parts[3]) if len(parts) > 3 else int(msg.get("camera_id"))
                self._enqueue(("inventory", cam_id, msg))
            elif kind == "health":
                cam_id = int(parts[3]) if len(parts) > 3 else int(msg.get("camera_id"))
                self._enqueue(("health", cam_id, msg))
        except Exception as e:  # noqa: BLE001
            log.warning("MQTT-Nachricht auf %s nicht verarbeitbar: %s", topic, e)

    def _enqueue(self, item) -> None:
        try:
            self.work.put_nowait(item)
        except queue.Full:
            log.error("Track-Warteschlange voll – Nachricht verworfen")

    def _work_loop(self) -> None:
        while True:
            kind, cam_id, msg = self.work.get()
            try:
                if kind == "track":
                    processor.handle_track(cam_id, msg)
                elif kind == "cmd_result":
                    from . import health
                    if not health.handle_cmd_result(msg):
                        processor.handle_cmd_result(msg)
                elif kind == "inventory":
                    from .inventory import memory as inventory_memory
                    inventory_memory.handle(cam_id, msg)
                elif kind == "health":
                    from . import health
                    health.handle(cam_id, msg)
                elif kind == "reset_counts":
                    processor.reset_counts()
                elif kind == "call":
                    cam_id()
            except Exception:
                log.exception("Verarbeitung fehlgeschlagen (%s)", kind)


bus = Bus()
