"""Flacker-Test A1: Wie stabil sind die Inventar-Zustände einer Kamera im Ruhezustand?

    python -m vg_worker.tests_feasibility.flickertest --camera 3 --minutes 10 [--out /out]

Liest nur mit (MQTT `vg/internal/inventory/{camera_id}`, voller Zustand je Durchlauf) und schreibt nichts.
Ausgewertet werden je Gegenstand (`item_id`) und je Label:
- Zustandswechsel present<->missing (Flackern), Anteil der Durchläufe mit echtem Treffer, Score-Schwankung,
- Kandidaten, die nie bestätigt werden (Rauschen),
- Bbox-Mittelpunkt-Drift (für die `moved`-Schwelle 0,1),
- `changes` des Workers (appeared/moved/disappeared), auf eine Stunde hochgerechnet.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import threading
import time
import uuid
from collections import defaultdict

import paho.mqtt.client as mqtt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=3)
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--host", default=os.environ.get("VG_MQTT_HOST", "mosquitto"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("VG_MQTT_PORT", "1883")))
    ap.add_argument("--out", default="/out")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    msgs: list[dict] = []
    lock = threading.Lock()
    t_start = time.time()

    def on_msg(_c, _u, m):
        if m.retain:  # alter Zustand vom Broker – nicht mitzählen
            return
        try:
            d = json.loads(m.payload)
        except Exception:
            return
        with lock:
            msgs.append(d)

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"vg-flickertest-{uuid.uuid4().hex[:8]}")
    c.on_message = on_msg
    c.connect(a.host, a.port, 30)
    c.subscribe(f"vg/internal/inventory/{a.camera}", qos=1)
    c.loop_start()
    end = t_start + a.minutes * 60
    while time.time() < end:
        time.sleep(30)
        with lock:
            n = len(msgs)
        print(f"[{int(time.time() - t_start):4d}s] {n} Durchläufe", flush=True)
    c.loop_stop()
    c.disconnect()

    rep = analyse(msgs, a.minutes)
    rep["summary"]["started"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_start))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, f"flicker_cam{a.camera}{a.tag}.json"), "w") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    print(json.dumps(rep["summary"], indent=1, ensure_ascii=False))
    for lab, s in sorted(rep["by_label"].items()):
        print(f"{lab:16s} items={s['items']:2d} present_share={s['present_share']:.2f} hit_rate={s['hit_rate']:.2f} "
              f"flips={s['flips']} score={s['score_mean']:.2f}±{s['score_sd']:.2f} drift_max={s['drift_max']:.3f} "
              f"never_confirmed={s['never_confirmed']}")


def analyse(msgs: list[dict], minutes: float) -> dict:
    sessions = {m.get("session") for m in msgs}
    runs = len(msgs)
    items: dict[str, dict] = {}
    changes: dict[str, int] = defaultdict(int)
    for m in msgs:
        for ch in m.get("changes") or []:
            changes[ch.get("type")] += 1
        for it in m.get("items") or []:
            r = items.setdefault(it["item_id"], {"label": it["label"], "states": [], "scores": [], "centers": [],
                                                 "hits": [], "occluded": 0, "runs": 0, "seq": ""})
            r["runs"] += 1
            r["states"].append(it["state"])
            r["scores"].append(it["score"])
            b = it["bbox"]
            r["centers"].append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
            hit = it.get("last_seen") == m.get("frame_ts")
            r["hits"].append(hit)
            r["occluded"] += bool(it.get("occluded"))
            r["seq"] += "O" if (it.get("occluded") and not hit) else ("H" if hit else "M")
    by_item = {}
    for iid, r in items.items():
        st = r["states"]
        flips = sum(1 for x, y in zip(st, st[1:]) if x != y and {x, y} == {"present", "missing"})
        c0 = r["centers"][0]
        drift = max(((x - c0[0]) ** 2 + (y - c0[1]) ** 2) ** 0.5 for x, y in r["centers"])
        by_item[iid] = {
            "label": r["label"], "runs": r["runs"], "seen_share": r["runs"] / max(runs, 1),
            "present": st.count("present"), "missing": st.count("missing"), "candidate": st.count("candidate"),
            "flips": flips, "hit_rate": sum(r["hits"]) / max(len(r["hits"]), 1),
            "score_mean": statistics.fmean(r["scores"]), "score_sd": statistics.pstdev(r["scores"]),
            "drift_max": drift, "occluded_runs": r["occluded"],
            "first_state": st[0], "last_state": st[-1], "seq": r["seq"],
        }
    by_label: dict[str, dict] = {}
    for lab in sorted({v["label"] for v in by_item.values()}):
        vs = [v for v in by_item.values() if v["label"] == lab]
        tot = sum(v["runs"] for v in vs)
        by_label[lab] = {
            "items": len(vs), "flips": sum(v["flips"] for v in vs),
            "present_share": sum(v["present"] for v in vs) / max(runs, 1),
            "hit_rate": sum(v["hit_rate"] * v["runs"] for v in vs) / max(tot, 1),
            "score_mean": sum(v["score_mean"] * v["runs"] for v in vs) / max(tot, 1),
            "score_sd": max(v["score_sd"] for v in vs), "drift_max": max(v["drift_max"] for v in vs),
            "never_confirmed": sum(1 for v in vs if v["present"] == 0 and v["missing"] == 0),
        }
    h = minutes / 60
    summary = {
        "runs": runs, "minutes": minutes, "sessions": len(sessions), "items_total": len(by_item),
        "items_present_whole_time": sum(1 for v in by_item.values() if v["present"] == runs),
        "items_with_flips": sum(1 for v in by_item.values() if v["flips"] > 0),
        "flips_total": sum(v["flips"] for v in by_item.values()),
        "candidates_never_confirmed": sum(1 for v in by_item.values() if v["present"] == 0 and v["missing"] == 0),
        "changes": dict(changes), "changes_per_hour": {k: round(v / h, 1) for k, v in changes.items()},
        "interval_s": statistics.median([m.get("interval_s", 0) for m in msgs]) if msgs else None,
        "model": msgs[-1].get("model") if msgs else None,
    }
    summary["simulated"] = simulate(by_item, minutes)
    return {"summary": summary, "by_label": by_label, "by_item": by_item}


def simulate(by_item: dict, minutes: float) -> dict:
    """Spielt die Treffer-Folgen (H=Treffer, M=Fehlschlag, O=verdeckt) mit anderen Hysterese-Werten nach:
    Zustandswechsel present->missing je Stunde für confirm_n/miss_m-Kombinationen (nur bestätigte Gegenstände,
    Gegenstände mit < 5 Durchläufen ignoriert). Hinweis: Folgen beginnen erst, wenn der Worker den Gegenstand führt."""
    out = {}
    h = minutes / 60
    for cn in (2, 3):
        for mm in (3, 5, 10, 20):
            gone = 0
            for v in by_item.values():
                seq = v.get("seq", "")
                if len(seq) < 5:
                    continue
                state, hits, miss = "candidate", 0, 0
                for ch in seq:
                    if ch == "H":
                        hits += 1
                        miss = 0
                        if state != "present" and hits >= cn:
                            state = "present"
                    elif ch == "M":
                        miss += 1
                        if state == "present" and miss >= mm:
                            state = "missing"
                            gone += 1
                            hits = 0
                        elif state == "candidate":
                            hits = 0
            out[f"confirm{cn}_miss{mm}"] = {"gone_events": gone, "gone_per_hour": round(gone / h, 1)}
    return out


if __name__ == "__main__":
    main()
