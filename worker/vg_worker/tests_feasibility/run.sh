#!/usr/bin/env bash
# Startet einen Machbarkeitstest im Test-Image (GPU nur unter der gemeinsamen Sperre ~/vg-gpu.lock).
#   worker/vg_worker/tests_feasibility/run.sh <modul> [args...]
#   z. B.  run.sh itemtest --fetch 60      run.sh posetest --parts load
# Umgebung:
#   VG_W0_OUT   Ausgabeordner (Default: ./w0-out)       VG_W0_DATA  Datenordner (Default: ./w0-data)
#   VG_W0_IMAGE Test-Image (Default: vg-w0-feasibility:test, siehe Dockerfile hier)
#   VG_W0_GPU=0 ohne GPU/Sperre (healthtest, flickertest)
# Mounts: Produktiv-Volumes `models` und `media` nur lesend; neue Gewichte landen im Test-Volume vg-w0_models.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
mod="$1"; shift
out="${VG_W0_OUT:-$PWD/w0-out}"; data="${VG_W0_DATA:-$PWD/w0-data}"
mkdir -p "$out" "$data"
docker volume create vg-w0_models >/dev/null
args=(--rm --name "vg-w0-$mod-$$" --network vision-guard_default
  -e HF_HOME=/wmodels/hf -e TORCH_HOME=/wmodels/torch -e XDG_CACHE_HOME=/wmodels/cache -e HOME=/wmodels/home
  -e YOLO_CONFIG_DIR=/wmodels/ultralytics
  -v vg-w0_models:/wmodels -v vision-guard_models:/models:ro -v vision-guard_media:/media:ro
  -v "$here":/app/vg_worker/tests_feasibility:ro -v "$repo/testcam/videos":/videos:ro
  -v "$data":/data -v "$out":/out)
if [ "${VG_W0_GPU:-1}" = "1" ]; then
  exec flock "$HOME/vg-gpu.lock" docker run --gpus all "${args[@]}" "${VG_W0_IMAGE:-vg-w0-feasibility:test}" \
    python -m "vg_worker.tests_feasibility.$mod" "$@"
else
  exec docker run "${args[@]}" "${VG_W0_IMAGE:-vg-w0-feasibility:test}" python -m "vg_worker.tests_feasibility.$mod" "$@"
fi
