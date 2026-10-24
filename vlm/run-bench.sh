#!/usr/bin/env bash
# VLM-Machbarkeitstest starten (unter der GPU-Sperre ~/vg-gpu.lock, neben dem laufenden Haupt-Stack).
#   vlm/run-bench.sh <fragensatz-ordner> <ausgabe-ordner> [bench.py-Argumente…]
# Fragensatz erzeugen:  worker/vg_worker/tests_feasibility/run.sh vlmset   (-> $VG_W0_DATA/vlmset)
# Image bauen:          docker build -t vg-w0-vlm:bench -f vlm/Dockerfile.bench vlm
# Gewichte liegen im Test-Volume vg-w0_models (nicht im Produktiv-Volume).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
set_dir="$(cd "$1" && pwd)"; out="$2"; shift 2
mkdir -p "$out"; out="$(cd "$out" && pwd)"
exec flock "$HOME/vg-gpu.lock" docker run --rm --name "vg-w0-vlmbench-$$" --gpus all \
  -v vg-w0_models:/models -v "$here/bench.py":/app/bench.py:ro -v "$set_dir":/set:ro -v "$out":/out \
  vg-w0-vlm:bench python /app/bench.py --set /set --out /out "$@"
