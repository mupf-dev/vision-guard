"""Bildmodelle des vlm-Dienstes (Adapter aus ``vlm/bench.py``, Revisionen gepinnt, messungen.md §6).

Läuft ausschließlich im Kindprozess (``engine._child``): nur dort entsteht ein CUDA-Kontext, sodass beim Entladen
(Prozessende) der komplette Speicher inkl. Kontext an den Worker zurückgeht.
"""
from __future__ import annotations

from typing import Any

# Setting-Wert (vlm_model) → (Repo, Revision, Lizenz-Hinweis)
MODELS: dict[str, tuple[str, str]] = {
    "moondream2-int4": ("moondream/moondream-2b-2025-04-14-4bit", "a89c59223ef8b5bb7826780728eeec172727ca84"),
    "smolvlm-500m": ("HuggingFaceTB/SmolVLM-500M-Instruct", "a7da5b986cb59b408707209984f360a5f4ad7e47"),
    "qwen25vl-3b-nf4": ("Qwen/Qwen2.5-VL-3B-Instruct", "66285546d2b821cf421d4f5eb2576359d3770cd3"),
}
# Moondream 2 INT4 lädt Tokenizer/Konfiguration zusätzlich aus diesem Repo (Remote-Code)
EXTRA_REPOS: dict[str, list[tuple[str, str | None]]] = {
    "moondream2-int4": [("vikhyatk/moondream2", None)],
}
# Bedarf laut Messung (torch reserviert + eigener CUDA-Kontext, MB) – Untergrenze für die Lease
NEED_MB = {"moondream2-int4": 3000, "smolvlm-500m": 1800, "qwen25vl-3b-nf4": 3000}
FALLBACK = "smolvlm-500m"      # wenn der Worker nicht genug VRAM freibekommt


class Moondream:
    def __init__(self, repo: str, rev: str):
        from transformers import AutoModelForCausalLM
        self.m = AutoModelForCausalLM.from_pretrained(repo, revision=rev, trust_remote_code=True,
                                                      device_map={"": "cuda"})

    def ask(self, img, q: str, max_tokens: int) -> str:
        try:
            r = self.m.query(img, q, settings={"max_tokens": int(max_tokens)})
        except TypeError:
            r = self.m.query(img, q)
        return str(r["answer"]).strip()


class SmolVLM:
    def __init__(self, repo: str, rev: str):
        import torch
        from transformers import AutoModelForVision2Seq, AutoProcessor
        self.torch = torch
        self.p = AutoProcessor.from_pretrained(repo, revision=rev)
        self.m = AutoModelForVision2Seq.from_pretrained(repo, revision=rev, torch_dtype=torch.bfloat16).cuda().eval()

    def ask(self, img, q: str, max_tokens: int) -> str:
        msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": q}]}]
        prompt = self.p.apply_chat_template(msgs, add_generation_prompt=True)
        x = self.p(text=prompt, images=[img], return_tensors="pt").to("cuda")
        with self.torch.inference_mode():
            out = self.m.generate(**x, max_new_tokens=int(max_tokens), do_sample=False)
        return self.p.batch_decode(out[:, x["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()


class QwenVL:
    def __init__(self, repo: str, rev: str):
        import torch
        from transformers import AutoProcessor, BitsAndBytesConfig, Qwen2_5_VLForConditionalGeneration
        self.torch = torch
        q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
                               bnb_4bit_use_double_quant=True)
        # Bild-Token begrenzen: max. ~ 640×640 Pixel (≈ 520 Token)
        self.p = AutoProcessor.from_pretrained(repo, revision=rev, min_pixels=256 * 28 * 28, max_pixels=640 * 640)
        self.m = Qwen2_5_VLForConditionalGeneration.from_pretrained(repo, revision=rev, quantization_config=q,
                                                                    device_map={"": "cuda"}).eval()

    def ask(self, img, q: str, max_tokens: int) -> str:
        msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": q}]}]
        prompt = self.p.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
        x = self.p(text=[prompt], images=[img], return_tensors="pt").to("cuda")
        with self.torch.inference_mode():
            out = self.m.generate(**x, max_new_tokens=int(max_tokens), do_sample=False)
        return self.p.batch_decode(out[:, x["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()


def build(key: str) -> Any:
    repo, rev = MODELS[key]
    if key.startswith("moondream"):
        return Moondream(repo, rev)
    if key.startswith("smolvlm"):
        return SmolVLM(repo, rev)
    if key.startswith("qwen"):
        return QwenVL(repo, rev)
    raise ValueError(key)


def download(key: str) -> None:
    """Gewichte einmalig ins Volume `models` laden (CPU, ohne GPU-Lease)."""
    from huggingface_hub import snapshot_download
    repo, rev = MODELS[key]
    try:                       # schon im Volume? Dann ohne Netz (sofort fertig)
        snapshot_download(repo, revision=rev, local_files_only=True)
        return
    except Exception:  # noqa: BLE001
        pass
    snapshot_download(repo, revision=rev)
    for r, rv in EXTRA_REPOS.get(key, []):
        try:
            snapshot_download(r, revision=rv, allow_patterns=["*.json", "*.txt", "*.model", "*.py"])
        except Exception:  # noqa: BLE001 – optional, Remote-Code lädt es sonst selbst
            pass
