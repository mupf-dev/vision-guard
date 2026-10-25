# Dienst `vlm` – „Frag die Kamera“ (Welle 3, A2)

Lokales Bildmodell für Fragen zum aktuellen Kamerabild („Ist das Licht an?“). Vertrag: CONTRACT.md W3.E
(Schnittstelle) und W3.C (GPU-Lease). Grundlage der Modellwahl: `docs/plan/roadmap/messungen.md` §6.

## Start

```bash
docker compose --profile vlm up -d --build vlm      # optional; ohne den Dienst antwortet nur die Cloud (falls freigegeben)
docker compose logs -f vlm
```

Beim ersten Start lädt der Dienst die Gewichte des eingestellten Modells ins Volume `models` (`hf/`, Moondream ≈ 2 GB,
nur CPU/Netz, keine GPU). Das Modell selbst kommt erst mit der ersten Frage in den Grafikspeicher.

## Ablauf

1. Backend `POST /api/cameras/{id}/ask` holt das Bild über go2rtc und ruft `POST http://vlm:8200/infer`.
2. Ist kein Modell geladen: Settings lesen (`GET /api/internal/settings`: `vlm_enabled`, `vlm_model`,
   `vlm_idle_unload_s`, `vlm_need_mb`), GPU-Lease anfragen (`vg/internal/gpu/request`, höchstens 10 s, sonst 503).
   Der Worker entlädt YOLOE (Inventar) und SigLIP (Zeitmaschine) und meldet den freien Speicher (NVML).
3. Das Modell lädt in einem **Kindprozess** mit Speichergrenze = frei − 300 MB. Beim Entladen endet der Prozess –
   so wird auch der CUDA-Kontext frei (gemessen: VRAM danach wieder exakt auf dem Ausgangswert).
4. Jede Frage verlängert die Lease (`max_s` = `vlm_idle_unload_s` + 60). Nach `vlm_idle_unload_s` ohne Frage:
   Kindprozess beenden, `release` → der Worker lädt YOLOE wieder.
5. Meldet der Worker `released` (z. B. Ablauf), entlädt der Dienst sofort.

Bekommt der Worker nicht genug Speicher frei („zu wenig VRAM“), weicht der Dienst auf **SmolVLM-500M** aus (1,8 GB);
reicht auch das nicht, gibt er die Lease zurück (503).

Grenzen: höchstens 1 laufende + 1 wartende Anfrage (sonst 429), Antwort-Zeitlimit 20 s (dann wird der Kindprozess
beendet), Ladezeitlimit 180 s. Bilder und Fragen werden weder gespeichert noch geloggt.

## Schnittstelle (nur intern, Port 8200)

| Pfad | Beschreibung |
|---|---|
| `POST /infer` | `{image_b64, question, region?: [x1,y1,x2,y2], mode: "query"\|"yesno", max_tokens?}` → `{answer, yes_no, ms, model, model_ms}` |
| `GET /health` | `{state: "off\|loading\|ready\|error: …", model, vram_mb, last_used, enabled, lease, mqtt, download, last_error, stats}` |
| `POST /unload` | Modell sofort entladen, GPU zurückgeben (Wartung/Tests) |

`mode:"yesno"` hängt „Answer with yes or no first, then explain in one short sentence.“ an; `yes_no` wird aus dem
ersten Wort gelesen (`unknown` erlaubt). Das Backend wählt `yesno` für Fragen, die mit is/are/do/can/… bzw.
ist/sind/gibt/… beginnen.

## Modelle (`vlm_model`, Revisionen gepinnt in `vg_vlm/models.py`)

| Setting | Modell | Bedarf | Hinweis |
|---|---|---|---|
| `moondream2-int4` (Standard) | moondream/moondream-2b-2025-04-14-4bit | ≈ 2,0 GB torch + Kontext | Apache-2.0, nur Englisch gut |
| `smolvlm-500m` | HuggingFaceTB/SmolVLM-500M-Instruct | ≈ 1,5 GB | Rückfall bei knappem VRAM |
| `qwen25vl-3b-nf4` | Qwen/Qwen2.5-VL-3B-Instruct (NF4) | ≈ 2,6 GB | versteht Deutsch; Lizenz nur nicht-kommerziell; ≈ 7 GB Download |

## Messwerte (25.10.2026, neben dem Haupt-Stack mit `cam_3` + Inventar, unter `flock ~/vg-gpu.lock`)

- Laden inkl. Lease 4,2–4,3 s (Lease + Prozessstart ≈ 1,7 s, Modell 2,5 s), erste Antwort 6,6 s, danach **1,0–1,4 s** je Frage
  (Bild 768×432; `cam_3` 2880×1616 laut W0 ≈ 1,5 s). Über das Backend kommt die Bildbeschaffung von go2rtc dazu
  (`cam_3` 0,6–2 s; Demo-Kameras bis 5 s, da go2rtc auf einen Keyframe wartet).
- VRAM gesamt: vorher 2,53 GB, Spitze **5,05 GB** (Grenze 5,6 GB), nach dem Entladen wieder 2,53 GB.
- Detektion `cam_3` während der Lease 4,66–5,16 fps (Grenze 4,5); Inventar `paused: GPU-Lease vlm`, danach `ready`.
- 5/5 Ja/Nein-Fragen an die Demo-Kameras richtig; 4 parallele Anfragen → 2 × 200, 2 × 429.
