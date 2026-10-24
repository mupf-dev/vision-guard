#!/usr/bin/env bash
# Zusätzliche Testvideos für die Machbarkeitstests und Wellen 2–3 (Sturz, Gesten, Regal) – optional.
# Ergebnis:
#   videos/sturz.mp4   -> testcam cam3: UR-Fall-Stürze (jeweils 10 s Standbild "liegt danach") + Alltagsszenen
#   videos/gesten.mp4  -> testcam cam4: UTD-MHAD-Armgesten (Winken, Arme verschränken, Wischen) + Negativ-Aktionen
#   videos/regal.mp4   -> testcam cam5: Intel "store-aisle-detection" + "bottle-detection" (Regal/Flaschen)
#   videos/raw/urfall, videos/raw/utd -> Einzelclips + Label-CSV für posetest
# Lizenzen (nur private/Forschungs-Nutzung, NICHT weitergeben, NICHT committen):
#   UR Fall Detection Dataset, Kwolek & Kępski 2014, CC BY-NC-SA 4.0  (https://fenix.ur.edu.pl/~mkepski/ds/uf.html)
#   UTD-MHAD, Chen/Jafari/Kehtarnavaz 2015, frei für Forschung, Zitat erbeten (https://personal.utdallas.edu/~kehtar/UTD-MHAD.html)
#   Intel IoT DevKit sample-videos, CC BY 4.0
# Download: ~85 MB (UR Fall) + ~100 Clips aus dem UTD-MHAD-ZIP per HTTP-Range + ~10 MB (Intel).
# Ohne UTD (Server sehr langsam):  SKIP_UTD=1 ./download-extra-videos.sh
set -euo pipefail
cd "$(dirname "$0")/videos"
mkdir -p raw/urfall raw/utd

FFMPEG=(ffmpeg)
if ! command -v ffmpeg >/dev/null 2>&1; then
  FFMPEG=(docker run --rm -v "$PWD:/w" -w /w --entrypoint ffmpeg bluenviron/mediamtx:1.15.3-ffmpeg)
fi
enc=(-an -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -r 15 -g 15)

# ---------------------------------------------------------------- UR Fall (Sturz)
ur=https://fenix.ur.edu.pl/~mkepski/ds/data
for f in urfall-cam0-falls.csv urfall-cam0-adls.csv; do
  [ -f "raw/urfall/$f" ] || curl -fsSL -o "raw/urfall/$f" "$ur/$f"
done
for i in $(seq -w 1 30); do
  [ -f "raw/urfall/fall-$i-cam0.mp4" ] || curl -fsSL -o "raw/urfall/fall-$i-cam0.mp4" "$ur/fall-$i-cam0.mp4"
done
for i in $(seq -w 1 40); do
  [ -f "raw/urfall/adl-$i-cam0.mp4" ] || curl -fsSL -o "raw/urfall/adl-$i-cam0.mp4" "$ur/adl-$i-cam0.mp4"
done

if [ ! -f sturz.mp4 ]; then
  # rechte Bildhälfte = RGB (links Tiefenbild), auf 640×480 hochskaliert; nach jedem Sturz 10 s Standbild
  tmp=$(mktemp -d ./tmp.XXXX); : > "$tmp/list.txt"
  n=0
  for i in 01 02 03 04 05 06 07 08 09 10; do
    "${FFMPEG[@]}" -v error -y -i "raw/urfall/fall-$i-cam0.mp4" \
      -vf "crop=320:240:320:0,scale=640:480,tpad=stop_mode=clone:stop_duration=10" "${enc[@]}" "$tmp/f$i.mp4"
    echo "file 'f$i.mp4'" >> "$tmp/list.txt"
    # dazwischen eine Alltagsszene (sitzen, bücken, auf Sofa/Bett legen)
    a=$(printf '%02d' $(( (n * 4) % 40 + 1 ))); n=$((n + 1))
    "${FFMPEG[@]}" -v error -y -i "raw/urfall/adl-$a-cam0.mp4" -vf "crop=320:240:320:0,scale=640:480" "${enc[@]}" "$tmp/a$a.mp4"
    echo "file 'a$a.mp4'" >> "$tmp/list.txt"
  done
  "${FFMPEG[@]}" -v error -y -f concat -safe 0 -i "$tmp/list.txt" -c copy sturz.mp4
  rm -rf "$tmp"
fi

# ---------------------------------------------------------------- UTD-MHAD (Gesten)
if [ "${SKIP_UTD:-0}" != "1" ]; then
  if ! ls raw/utd/a6_s1_t1_color.avi >/dev/null 2>&1; then
    # Der Server liefert je Verbindung nur ~40 KB/s; statt des ganzen ZIPs (1,1 GB) werden nur die benötigten Clips
    # per HTTP-Range geladen (remote-unzip.py, parallel). Aktionen: 1/2 Wischen, 3 Winken, 4 Klatschen,
    # 6 Arme verschränken, 16 Curl, 17 Aufschlag, 22 Joggen, 23 Gehen, 24/25 Aufstehen/Hinsetzen, 27 Kniebeuge;
    # Personen 1–8, Durchgang 1
    pats=()
    for a in 1 2 3 4 6 16 17 22 23 24 25 27; do for s in 1 2 3 4 5 6 7 8; do pats+=("*a${a}_s${s}_t1_color.avi"); done; done
    python3 ../remote-unzip.py https://personal.utdallas.edu/~kehtar/UTD-MAD/RGB.zip raw/utd "${pats[@]}"
  fi
  if [ ! -f gesten.mp4 ]; then
    tmp=$(mktemp -d ./tmp.XXXX); : > "$tmp/list.txt"
    for s in 1 2 3 4; do for a in 3 6 1 2 4 24 25 23; do
      src="raw/utd/a${a}_s${s}_t1_color.avi"; [ -f "$src" ] || continue
      "${FFMPEG[@]}" -v error -y -i "$src" -vf "scale=640:480" "${enc[@]}" "$tmp/a${a}_s${s}.mp4"
      echo "file 'a${a}_s${s}.mp4'" >> "$tmp/list.txt"
    done; done
    "${FFMPEG[@]}" -v error -y -f concat -safe 0 -i "$tmp/list.txt" -c copy gesten.mp4
    rm -rf "$tmp"
  fi
fi

# ---------------------------------------------------------------- Intel (Regal)
base=https://github.com/intel-iot-devkit/sample-videos/raw/master
for v in store-aisle-detection bottle-detection; do
  [ -f "raw/$v.mp4" ] || curl -fsSL -o "raw/$v.mp4" "$base/$v.mp4"
done
if [ ! -f regal.mp4 ]; then
  tmp=$(mktemp -d ./tmp.XXXX)
  "${FFMPEG[@]}" -v error -y -i raw/store-aisle-detection.mp4 -vf "scale=1280:-2" "${enc[@]}" "$tmp/s.mp4"
  "${FFMPEG[@]}" -v error -y -i raw/bottle-detection.mp4 -vf "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2" "${enc[@]}" "$tmp/b.mp4"
  printf "file 's.mp4'\nfile 'b.mp4'\n" > "$tmp/list.txt"
  "${FFMPEG[@]}" -v error -y -f concat -safe 0 -i "$tmp/list.txt" -c copy regal.mp4
  rm -rf "$tmp"
fi
ls -lh ./*.mp4
