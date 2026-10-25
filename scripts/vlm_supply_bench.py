"""Messwerkzeug C4-VLM-Modus: zählt Artikel bzw. schätzt Füllstände über den Dienst `vlm` (A2-Schnittstelle
`POST /infer`) und vergleicht mit der Wahrheit. Ergebnisse: docs/plan/roadmap/messungen.md (Welle 4, C4-VLM).

Aufruf (Wegwerf-Container im Netz des Stacks, GPU-Lease holt sich der Dienst selbst; trotzdem unter der Sperre):
    flock ~/vg-gpu.lock docker run --rm --network vision-guard_default \
        -v "$PWD/backend/app/vlm_measure.py:/b/vlm_measure.py:ro" -v "$PWD/scripts:/s:ro" -v <set>:/set:ro \
        vision-guard-backend python /s/vlm_supply_bench.py --set /set --out /set/result.json

`<set>/truth.json` = `[{file, measure: "count"|"fill", item: "bottles", truth: 3 | 0.5}]`.
Es werden keine Bilder gespeichert; das Ergebnis enthält nur Antworten, Zahlen und Zeiten.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import sys
import time

import httpx

sys.path.insert(0, "/b")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "app"))
import vlm_measure as vm  # noqa: E402

VARIANTS = {
    "count": [None, "How many {item} are visible?"],
    "fill": [None, "What percentage of the {item} is filled?"],
}
LEVEL_PARAMS = {"min_count": 2, "low_below": 0.4, "out_below": 0.15}


def ask(url: str, img: bytes, q: str, mode: str = "query") -> tuple[str, float]:
    t0 = time.perf_counter()
    for _ in range(5):
        r = httpx.post(f"{url}/infer", timeout=60, json={"image_b64": base64.b64encode(img).decode(),
                                                          "question": q, "mode": mode})
        if r.status_code == 429:
            time.sleep(1)
            continue
        r.raise_for_status()
        return str(r.json().get("answer") or ""), (time.perf_counter() - t0) * 1000
    raise RuntimeError("vlm dauerhaft beschäftigt")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--url", default=os.environ.get("VG_VLM_URL", "http://vlm:8200"))
    ap.add_argument("--only", choices=("count", "fill"), default=None)
    ap.add_argument("--probe", action="append", default=[],
                    help="zusätzliche Ja/Nein-Frage je Bild, z. B. 'Is the {item} empty or almost empty?'")
    a = ap.parse_args()
    truth = json.load(open(os.path.join(a.set, "truth.json")))
    if a.only:
        truth = [t for t in truth if t["measure"] == a.only]
    if a.probe:
        for t in truth:
            img = open(os.path.join(a.set, t["file"]), "rb").read()
            for q in a.probe:
                ans, ms = ask(a.url, img, q.format(item=t["item"]), "yesno")
                print(f"{t['file']:<16} wahr={t['truth']:<5} {round(ms):>5} ms {q[:40]!r}: {ans[:60]!r}", flush=True)
        return
    rows = []
    for t in truth:
        img = open(os.path.join(a.set, t["file"]), "rb").read()
        for vi, tpl in enumerate(VARIANTS[t["measure"]]):
            q = vm.build_question(t["measure"], t["item"], tpl.format(item=t["item"]) if tpl else "")
            ans, ms = ask(a.url, img, q)
            val = vm.parse(t["measure"], ans)
            st_true = vm.level_state(t["measure"], float(t["truth"]), LEVEL_PARAMS)
            st = vm.level_state(t["measure"], val, LEVEL_PARAMS) if val is not None else None
            row = {**t, "variant": vi, "question": q, "answer": ans[:200], "value": val, "ms": round(ms),
                   "state": st, "state_true": st_true, "state_ok": st == st_true}
            rows.append(row)
            print(f"{t['file']:<16} v{vi} wahr={t['truth']:<5} wert={val!s:<5} {st_true:>3}/{st!s:<4} "
                  f"{round(ms):>5} ms  {ans[:70]!r}", flush=True)
    summary = {}
    for meas in ("count", "fill"):
        for vi in (0, 1):
            rs = [r for r in rows if r["measure"] == meas and r["variant"] == vi]
            if not rs:
                continue
            parsed = [r for r in rs if r["value"] is not None]
            s = {"n": len(rs), "parsed": len(parsed), "state_ok": sum(r["state_ok"] for r in rs),
                 "ms_median": round(statistics.median(r["ms"] for r in rs))}
            if meas == "count":
                s["exact"] = sum(1 for r in parsed if r["value"] == r["truth"])
                s["within1"] = sum(1 for r in parsed if abs(r["value"] - r["truth"]) <= 1)
                for kind in sorted({r["kind"] for r in rs}):
                    k = [r for r in rs if r["kind"] == kind]
                    s[f"exact_{kind}"] = f"{sum(1 for r in k if r['value'] == r['truth'])}/{len(k)}"
            else:
                s["abs_err_median"] = round(statistics.median(abs(r["value"] - r["truth"]) for r in parsed), 3) \
                    if parsed else None
            summary[f"{meas}_v{vi}"] = s
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    if a.out:
        json.dump({"summary": summary, "rows": rows}, open(a.out, "w"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
