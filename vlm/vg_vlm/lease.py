"""GPU-Lease-Client des vlm-Dienstes (CONTRACT.md W3.C). Vorlage: ``worker/vg_worker/gpu_lease.LeaseClient``.

Zusätzlich zur Vorlage: verbindet sich asynchron (Mosquitto darf später starten), abonniert nach jedem
Reconnect neu und meldet ein ``released`` des Workers für die eigene Lease (z. B. ``reason:"expired"``) per
Callback, damit der Dienst das Modell sofort entlädt.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Callable, Optional

import paho.mqtt.client as mqtt

log = logging.getLogger("vg.vlm.lease")

TOPIC_REQUEST = "vg/internal/gpu/request"
TOPIC_GRANTED = "vg/internal/gpu/granted"
TOPIC_RELEASE = "vg/internal/gpu/release"
TOPIC_RELEASED = "vg/internal/gpu/released"


class LeaseClient:
    def __init__(self, host: str, port: int = 1883, holder: str = "vlm",
                 on_lost: Optional[Callable[[dict], None]] = None):
        self.holder = holder
        self.on_lost = on_lost
        self.request_id: Optional[str] = None
        self.last_grant: Optional[dict] = None
        self._granted: dict[str, dict] = {}
        self._released: dict[str, dict] = {}
        self._cv = threading.Condition()
        self.connected = False
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"vg-vlm-{uuid.uuid4().hex[:8]}")
        self.c.on_connect = self._on_connect
        self.c.on_disconnect = self._on_disconnect
        self.c.on_message = self._on_message
        self.c.reconnect_delay_set(1, 15)
        self.c.connect_async(host, port, keepalive=30)
        self.c.loop_start()

    # ------------------------------------------------------------------ MQTT
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = not getattr(reason_code, "is_failure", False)
        if self.connected:
            client.subscribe([(TOPIC_GRANTED, 1), (TOPIC_RELEASED, 1)])
            log.info("MQTT verbunden")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False

    def _on_message(self, client, userdata, msg):
        try:
            p = json.loads(msg.payload or b"{}")
        except ValueError:
            return
        if not isinstance(p, dict) or str(p.get("holder") or "") != self.holder:
            return
        rid = str(p.get("request_id") or "")
        lost = None
        with self._cv:
            if msg.topic == TOPIC_GRANTED:
                self._granted[rid] = p
            elif msg.topic == TOPIC_RELEASED:
                self._released[rid] = p
                if rid and rid == self.request_id:
                    # Worker hat die Lease beendet (abgelaufen/Neustart) – sie gilt nicht mehr
                    self.request_id = None
                    lost = p
            for d in (self._granted, self._released):     # klein halten
                if len(d) > 50:
                    for k in list(d)[:-20]:
                        d.pop(k, None)
            self._cv.notify_all()
        if lost is not None and self.on_lost is not None:
            try:
                self.on_lost(lost)
            except Exception:  # noqa: BLE001
                log.exception("on_lost fehlgeschlagen")

    # ------------------------------------------------------------------ API
    @property
    def held(self) -> bool:
        return self.request_id is not None

    def acquire(self, need_mb: int, max_s: float = 300.0, timeout: float = 10.0) -> Optional[dict]:
        """Lease anfragen bzw. verlängern (gleiche request_id). Liefert `granted` oder None (Zeitlimit)."""
        with self._cv:
            rid = self.request_id or uuid.uuid4().hex
            self.request_id = rid
            self._granted.pop(rid, None)
        self.c.publish(TOPIC_REQUEST, json.dumps({"request_id": rid, "holder": self.holder,
                                                  "need_mb": int(need_mb), "max_s": float(max_s)}), qos=1)
        deadline = time.time() + timeout
        with self._cv:
            while rid not in self._granted:
                left = deadline - time.time()
                if left <= 0:
                    return None
                self._cv.wait(left)
            g = self._granted[rid]
            if not g.get("ok") and str(g.get("error") or "").startswith("busy"):
                # anderer Halter aktiv: wir halten nichts
                if self.request_id == rid:
                    self.request_id = None
            self.last_grant = g
            return g

    def release(self, timeout: float = 5.0) -> Optional[dict]:
        with self._cv:
            rid = self.request_id
            self.request_id = None
        if not rid:
            return None
        self.c.publish(TOPIC_RELEASE, json.dumps({"request_id": rid, "holder": self.holder}), qos=1)
        deadline = time.time() + timeout
        with self._cv:
            while rid not in self._released:
                left = deadline - time.time()
                if left <= 0:
                    break
                self._cv.wait(left)
            return self._released.get(rid)

    def close(self) -> None:
        try:
            self.c.loop_stop()
            self.c.disconnect()
        except Exception:  # noqa: BLE001
            pass
