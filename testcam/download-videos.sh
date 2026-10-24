#!/usr/bin/env bash
# Lädt die Testvideos für die simulierten Kameras (Intel IoT DevKit sample-videos, CC BY 4.0).
set -euo pipefail
cd "$(dirname "$0")/videos"
base=https://github.com/intel-iot-devkit/sample-videos/raw/master
for v in face-demographics-walking person-bicycle-car-detection; do
  [ -f "$v.mp4" ] || curl -fsSL -o "$v.mp4" "$base/$v.mp4"
done
ls -lh *.mp4
