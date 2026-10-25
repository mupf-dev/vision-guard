"""Prüfwerkzeug für die GPU-Lease (W3.C): Dummy-Anfrage stellen, halten, freigeben.

    docker compose exec worker python -m vg_worker.leasetest --need-mb 3000 --hold 5

Zeigt `granted` (entladene Modelle, freier VRAM laut NVML), den Status von `models.inventory` während der Lease
und nach der Freigabe, ob YOLOE wieder geladen wurde. Braucht selbst keine GPU (nur MQTT).
"""
from __future__ import annotations

import argparse
import json
import threading
import time

import paho.mqtt.client as mqtt

from . import settings
from .gpu_lease import LeaseClient


def _system_watcher(host: str, port: int):
    last: dict = {}
    ev = threading.Event()

    def on_msg(c, u, m):
        try:
            last.update(json.loads(m.payload or b"{}"))
            ev.set()
        except ValueError:
            pass
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"vg-leasetest-sys-{int(time.time())}")
    c.on_message = on_msg
    c.connect(host, port, 30)
    c.subscribe("vg/internal/system", 0)
    c.loop_start()
    return c, last, ev


def main() -> None:
    ap = argparse.ArgumentParser(description="GPU-Lease mit Dummy-Halter testen")
    ap.add_argument("--need-mb", type=int, default=3000)
    ap.add_argument("--hold", type=float, default=5.0, help="Sekunden halten")
    ap.add_argument("--max-s", type=float, default=60.0)
    ap.add_argument("--holder", default="leasetest")
    ap.add_argument("--wait-reload", type=float, default=60.0, help="so lange auf models.inventory=ready warten")
    a = ap.parse_args()
    host, port = settings.MQTT_HOST, settings.MQTT_PORT
    sc, sysmsg, ev = _system_watcher(host, port)
    ev.wait(10)
    print("vorher:  inventory =", (sysmsg.get("models") or {}).get("inventory"),
          "| gpu mem_used =", (sysmsg.get("gpu") or {}).get("mem_used"), "MiB")
    lc = LeaseClient(host, port, holder=a.holder)
    t0 = time.time()
    g = lc.acquire(a.need_mb, max_s=a.max_s, timeout=10)
    if g is None:
        print("FEHLER: keine Antwort binnen 10 s (→ 503)")
        raise SystemExit(1)
    print(f"granted nach {time.time() - t0:.2f}s:", json.dumps({k: g.get(k) for k in
          ("ok", "free_mb", "need_mb", "unloaded", "ms", "error")}, ensure_ascii=False))
    time.sleep(min(a.hold, 6.0))
    ev.clear()
    ev.wait(7)
    print("während: inventory =", (sysmsg.get("models") or {}).get("inventory"),
          "| gpu_lease =", sysmsg.get("gpu_lease"))
    time.sleep(max(0.0, a.hold - 6.0))
    r = lc.release()
    print("released:", r)
    deadline = time.time() + a.wait_reload
    state = None
    while time.time() < deadline:
        ev.clear()
        ev.wait(6)
        state = (sysmsg.get("models") or {}).get("inventory")
        if state == "ready" and not sysmsg.get("gpu_lease"):
            break
    print("nachher: inventory =", state, "| gpu_lease =", sysmsg.get("gpu_lease"),
          "| gpu mem_used =", (sysmsg.get("gpu") or {}).get("mem_used"), "MiB")
    lc.close()
    sc.loop_stop()
    sc.disconnect()


if __name__ == "__main__":
    main()
