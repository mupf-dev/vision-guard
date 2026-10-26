# Service `vlm` – "Frag die Kamera" (Ask the camera) (wave 3, A2)

Local vision model for questions about the current camera image ("Ist das Licht an?" – "Is the light on?"). Contract:
CONTRACT.md (German) W3.E (interface) and W3.C (GPU lease). Basis for the model choice: `docs/plan/roadmap/messungen.md`
(German) §6.

## Start

```bash
docker compose --profile vlm up -d --build vlm      # optional; without the service only the cloud answers (if allowed)
docker compose logs -f vlm
```

On first start the service downloads the weights of the configured model into the volume `models` (`hf/`, Moondream ≈ 2 GB,
CPU/network only, no GPU). The model itself only goes into graphics memory with the first question.

## Flow

1. Backend `POST /api/cameras/{id}/ask` fetches the image via go2rtc and calls `POST http://vlm:8200/infer`.
2. If no model is loaded: read settings (`GET /api/internal/settings`: `vlm_enabled`, `vlm_model`,
   `vlm_idle_unload_s`, `vlm_need_mb`), request a GPU lease (`vg/internal/gpu/request`, at most 10 s, otherwise 503).
   The worker unloads YOLOE (inventory) and SigLIP (time machine) and reports the free memory (NVML).
3. The model loads in a **child process** with memory limit = free − 300 MB. On unload the process exits –
   this also frees the CUDA context (measured: VRAM afterwards back exactly at the initial value).
4. Every question extends the lease (`max_s` = `vlm_idle_unload_s` + 60). After `vlm_idle_unload_s` without a question:
   terminate the child process, `release` → the worker loads YOLOE again.
5. If the worker reports `released` (e.g. expiry), the service unloads immediately.

If the worker cannot free enough memory ("zu wenig VRAM" – not enough VRAM), the service falls back to **SmolVLM-500M** (1.8 GB);
if that is not enough either, it returns the lease (503).

Limits: at most 1 running + 1 waiting request (otherwise 429), answer time limit 20 s (then the child process is
terminated), load time limit 180 s. Images and questions are neither stored nor logged.

## Interface (internal only, port 8200)

| Path | Description |
|---|---|
| `POST /infer` | `{image_b64, question, region?: [x1,y1,x2,y2], mode: "query"\|"yesno", max_tokens?}` → `{answer, yes_no, ms, model, model_ms}` |
| `GET /health` | `{state: "off\|loading\|ready\|error: …", model, vram_mb, last_used, enabled, lease, mqtt, download, last_error, stats}` |
| `POST /unload` | Unload the model immediately, return the GPU (maintenance/tests) |

`mode:"yesno"` appends "Answer with yes or no first, then explain in one short sentence."; `yes_no` is read from the
first word (`unknown` allowed). The backend chooses `yesno` for questions starting with is/are/do/can/… or
ist/sind/gibt/….

## Models (`vlm_model`, revisions pinned in `vg_vlm/models.py`)

| Setting | Model | Requirement | Note |
|---|---|---|---|
| `moondream2-int4` (default) | moondream/moondream-2b-2025-04-14-4bit | ≈ 2.0 GB torch + context | Apache-2.0, good in English only |
| `smolvlm-500m` | HuggingFaceTB/SmolVLM-500M-Instruct | ≈ 1.5 GB | Fallback when VRAM is tight |
| `qwen25vl-3b-nf4` | Qwen/Qwen2.5-VL-3B-Instruct (NF4) | ≈ 2.6 GB | understands German; license non-commercial only; ≈ 7 GB download |

## Measurements (2026-10-25, alongside the main stack with `cam_3` + inventory, under `flock ~/vg-gpu.lock`)

- Loading incl. lease 4.2–4.3 s (lease + process start ≈ 1.7 s, model 2.5 s), first answer 6.6 s, then **1.0–1.4 s** per question
  (image 768×432; `cam_3` 2880×1616 per W0 ≈ 1.5 s). Via the backend, image retrieval from go2rtc is added
  (`cam_3` 0.6–2 s; demo cameras up to 5 s, since go2rtc waits for a keyframe).
- Total VRAM: before 2.53 GB, peak **5.05 GB** (limit 5.6 GB), after unloading back at 2.53 GB.
- Detection on `cam_3` during the lease 4.66–5.16 fps (limit 4.5); inventory `paused: GPU-Lease vlm`, then `ready`.
- 5/5 yes/no questions to the demo cameras correct; 4 parallel requests → 2 × 200, 2 × 429.
