"""VLM-Machbarkeitstest (A2 „Frag die Kamera“, W0-b): VRAM-Spitze, Ladezeit, Antwortzeit, Trefferquote.

    flock ~/vg-gpu.lock docker run --rm --gpus all -v vg-w0_models:/models -v <set>:/set:ro -v <out>:/out \
        vg-w0-vlm:bench python /app/bench.py --set /set --models moondream-int4,smolvlm-500m,qwen25vl-3b-nf4

Läuft **neben** dem produktiven Worker (inkl. YOLOE). Damit dieser nie wegen des Tests in einen OOM läuft, begrenzt
der Test den eigenen Prozess per `set_per_process_memory_fraction` auf (frei − `--margin-mb`). Ein OOM im Test ist dann
ein Ergebnis („passt nicht neben der Grundlast“), kein Ausfall des Haupt-Stacks.

Je Modell: Gerätespeicher vor dem Laden (= Grundlast), Spitze beim Laden und bei den Fragen (NVML, 20 ms), torch-Spitze,
Ladezeit, Antwortzeit je Frage (Median/p90; Bild in Originalgröße und hochskaliert auf 2880×1616 wie cam_3),
Ja/Nein-Trefferquote auf dem Fragensatz aus `vg_worker.tests_feasibility.vlmset`.
Modelle (Revisionen gepinnt):
- moondream-int4  moondream/moondream-2b-2025-04-14-4bit (Apache-2.0, QAT INT4, torchao)
- smolvlm-500m    HuggingFaceTB/SmolVLM-500M-Instruct (Apache-2.0, bf16)
- smolvlm-256m    HuggingFaceTB/SmolVLM-256M-Instruct (Apache-2.0, bf16)
- qwen25vl-3b-nf4 Qwen/Qwen2.5-VL-3B-Instruct (Qwen-Research-Lizenz, nicht kommerziell) mit bitsandbytes NF4
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import threading
import time
import traceback

import torch
from PIL import Image

MiB = 1024 * 1024
MODELS = {
    "moondream-int4": ("moondream/moondream-2b-2025-04-14-4bit", "a89c59223ef8b5bb7826780728eeec172727ca84"),
    "smolvlm-500m": ("HuggingFaceTB/SmolVLM-500M-Instruct", "a7da5b986cb59b408707209984f360a5f4ad7e47"),
    "smolvlm-256m": ("HuggingFaceTB/SmolVLM-256M-Instruct", "7e3e67edbbed1bf9888184d9df282b700a323964"),
    "qwen25vl-3b-nf4": ("Qwen/Qwen2.5-VL-3B-Instruct", "66285546d2b821cf421d4f5eb2576359d3770cd3"),
}


class Probe:
    def __init__(self):
        import pynvml
        pynvml.nvmlInit()
        self.nv = pynvml
        self.h = pynvml.nvmlDeviceGetHandleByIndex(0)
        self.peak = 0
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def used(self):
        return self.nv.nvmlDeviceGetMemoryInfo(self.h).used

    def total(self):
        return self.nv.nvmlDeviceGetMemoryInfo(self.h).total

    def _run(self):
        while not self._stop.is_set():
            try:
                self.peak = max(self.peak, self.used())
            except Exception:
                pass
            time.sleep(0.02)

    def reset(self):
        self.peak = self.used()


# ------------------------------------------------------------------ Adapter
class Moondream:
    def __init__(self, repo, rev, compile_=False):
        from transformers import AutoModelForCausalLM
        self.m = AutoModelForCausalLM.from_pretrained(repo, revision=rev, trust_remote_code=True,
                                                      device_map={"": "cuda"})
        if compile_:
            self.m.model.compile()

    def ask(self, img, q):
        return self.m.query(img, q)["answer"]


class SmolVLM:
    def __init__(self, repo, rev):
        from transformers import AutoModelForVision2Seq, AutoProcessor
        self.p = AutoProcessor.from_pretrained(repo, revision=rev)
        self.m = AutoModelForVision2Seq.from_pretrained(repo, revision=rev, torch_dtype=torch.bfloat16).cuda().eval()

    def ask(self, img, q):
        msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": q}]}]
        prompt = self.p.apply_chat_template(msgs, add_generation_prompt=True)
        x = self.p(text=prompt, images=[img], return_tensors="pt").to("cuda")
        with torch.inference_mode():
            out = self.m.generate(**x, max_new_tokens=24, do_sample=False)
        return self.p.batch_decode(out[:, x["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()


class QwenVL:
    def __init__(self, repo, rev):
        from transformers import AutoProcessor, BitsAndBytesConfig, Qwen2_5_VLForConditionalGeneration
        q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
                               bnb_4bit_use_double_quant=True)
        # Bild-Token begrenzen: max. ~ 640×640 Pixel (≈ 520 Token)
        self.p = AutoProcessor.from_pretrained(repo, revision=rev, min_pixels=256 * 28 * 28, max_pixels=640 * 640)
        self.m = Qwen2_5_VLForConditionalGeneration.from_pretrained(repo, revision=rev, quantization_config=q,
                                                                    device_map={"": "cuda"}).eval()

    def ask(self, img, q):
        msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": q}]}]
        prompt = self.p.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
        x = self.p(text=[prompt], images=[img], return_tensors="pt").to("cuda")
        with torch.inference_mode():
            out = self.m.generate(**x, max_new_tokens=24, do_sample=False)
        return self.p.batch_decode(out[:, x["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()


def build(key: str, compile_: bool):
    repo, rev = MODELS[key]
    if key.startswith("moondream"):
        return Moondream(repo, rev, compile_)
    if key.startswith("smolvlm"):
        return SmolVLM(repo, rev)
    if key.startswith("qwen"):
        return QwenVL(repo, rev)
    raise ValueError(key)


def yes_no(ans: str) -> str:
    a = ans.strip().lower().lstrip("*\"' ")
    if a.startswith("yes") or a.startswith("ja"):
        return "yes"
    if a.startswith("no") or a.startswith("nein"):
        return "no"
    return "unknown"


def run_model(key: str, qs: list, set_dir: str, probe: Probe, a) -> dict:
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    free, total = torch.cuda.mem_get_info()
    cap = max(0.05, (free - a.margin_mb * MiB) / total)
    torch.cuda.set_per_process_memory_fraction(cap)
    res: dict = {"model": key, "repo": MODELS[key][0], "revision": MODELS[key][1],
                 "device_before_mib": round(probe.used() / MiB), "cap_mib": round(cap * total / MiB)}
    probe.reset()
    t0 = time.time()
    try:
        m = build(key, a.compile)
    except Exception as e:  # noqa: BLE001
        res["error"] = f"Laden: {type(e).__name__}: {str(e)[:400]}"
        res["device_peak_mib"] = round(probe.peak / MiB)
        traceback.print_exc()
        return res
    res["load_s"] = round(time.time() - t0, 1)
    res["device_after_load_mib"] = round(probe.used() / MiB)
    res["torch_alloc_after_load_mib"] = round(torch.cuda.memory_allocated() / MiB)
    answers, times = [], []
    try:
        # Aufwärmen (erste Anfrage enthält ggf. Kernel-Kompilierung) – separat gemessen
        img0 = Image.open(os.path.join(set_dir, qs[0]["image"])).convert("RGB")
        t = time.time()
        m.ask(img0, qs[0]["question"] + " Answer yes or no.")
        res["first_answer_s"] = round(time.time() - t, 2)
        for q in qs:
            img = Image.open(os.path.join(set_dir, q["image"])).convert("RGB")
            t = time.time()
            ans = m.ask(img, q["question"] + " Answer yes or no.")
            dt = time.time() - t
            times.append(dt)
            yn = yes_no(ans)
            answers.append({**q, "answer": ans[:120], "yn": yn, "ok": yn == q["truth"], "s": round(dt, 2)})
            print(f"  [{key}] {q['image']}: {q['question']} -> {ans[:60]!r} ({yn}, Wahrheit {q['truth']}) {dt:.2f}s", flush=True)
        # große Auflösung wie cam_3 (2880×1616)
        big = img0.resize((2880, 1616))
        bt = []
        for _ in range(3):
            t = time.time()
            m.ask(big, "Describe the scene in one short sentence.")
            bt.append(time.time() - t)
        res["caption_2880_s_median"] = round(statistics.median(bt), 2)
        # freie Frage (kurze Antwort, keine Ja/Nein-Wertung)
        t = time.time()
        res["free_question_example"] = m.ask(img0, "What objects are visible? Answer briefly.")[:200]
        res["free_question_s"] = round(time.time() - t, 2)
    except Exception as e:  # noqa: BLE001
        res["error"] = f"Inferenz: {type(e).__name__}: {str(e)[:400]}"
        traceback.print_exc()
    res["device_peak_mib"] = round(probe.peak / MiB)
    res["device_delta_peak_mib"] = res["device_peak_mib"] - res["device_before_mib"]
    res["torch_alloc_peak_mib"] = round(torch.cuda.max_memory_allocated() / MiB)
    res["torch_reserved_peak_mib"] = round(torch.cuda.max_memory_reserved() / MiB)
    if times:
        st = sorted(times)
        res["answer_s_median"] = round(statistics.median(st), 2)
        res["answer_s_p90"] = round(st[int(0.9 * (len(st) - 1))], 2)
        res["accuracy"] = round(sum(x["ok"] for x in answers) / len(answers), 3)
        res["unknown"] = sum(x["yn"] == "unknown" for x in answers)
    res["answers"] = answers
    del m
    gc.collect()
    torch.cuda.empty_cache()
    res["device_after_unload_mib"] = round(probe.used() / MiB)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="/set")
    ap.add_argument("--models", default="moondream-int4,smolvlm-500m,qwen25vl-3b-nf4")
    ap.add_argument("--margin-mb", type=int, default=350, help="mindestens so viel VRAM bleibt für den Worker frei")
    ap.add_argument("--compile", action="store_true")
    ap.add_argument("--out", default="/out")
    a = ap.parse_args()
    qs = json.load(open(os.path.join(a.set, "questions.json")))
    probe = Probe()
    torch.cuda.init()
    rep = {"gpu_total_mib": round(probe.total() / MiB), "device_used_before_cuda_mib": None,
           "torch": torch.__version__, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    import transformers
    rep["transformers"] = transformers.__version__
    try:
        import torchao
        rep["torchao"] = torchao.__version__
    except Exception as e:  # noqa: BLE001
        rep["torchao"] = f"fehlt: {e}"
    torch.zeros(1, device="cuda")
    rep["device_used_with_own_context_mib"] = round(probe.used() / MiB)
    rep["questions"] = len(qs)
    for key in a.models.split(","):
        print(f"== {key}", flush=True)
        r = run_model(key, qs, a.set, probe, a)
        rep[key] = r
        print(json.dumps({k: v for k, v in r.items() if k != "answers"}, ensure_ascii=False), flush=True)
        os.makedirs(a.out, exist_ok=True)
        json.dump(rep, open(os.path.join(a.out, "vlm_bench.json"), "w"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
