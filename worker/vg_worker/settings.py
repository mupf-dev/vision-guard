"""Worker configuration from environment variables."""
from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


# --- services -------------------------------------------------------------
BACKEND_URL = _env("VG_BACKEND_URL", "http://backend:8000").rstrip("/")
MQTT_HOST = _env("VG_MQTT_HOST", "mosquitto")
MQTT_PORT = _int("VG_MQTT_PORT", 1883)
GO2RTC_RTSP = _env("VG_GO2RTC_RTSP", "rtsp://go2rtc:8554").rstrip("/")

# --- paths ----------------------------------------------------------------
MEDIA_DIR = _env("VG_MEDIA_DIR", "/data/media")
MODELS_DIR = _env("VG_MODELS_DIR", "/models")
BUFFER_DIR = _env("VG_BUFFER_DIR", "/buffer")

# --- ingest ---------------------------------------------------------------
MAX_FRAME_WIDTH = _int("VG_MAX_FRAME_WIDTH", 1920)      # decoded frame (crops/snapshots)
HWACCEL = _env("VG_HWACCEL", "none").lower()            # none | cuda | auto
STALL_TIMEOUT_S = _float("VG_STALL_TIMEOUT", 10.0)
FIRST_FRAME_TIMEOUT_S = _float("VG_FIRST_FRAME_TIMEOUT", 30.0)

# --- ring buffer ----------------------------------------------------------
BUFFER_SEGMENT_S = _float("VG_BUFFER_SEGMENT_S", 2.0)
BUFFER_KEEP_S = _float("VG_BUFFER_KEEP_S", 30.0)
RECORDER_ENABLED = _bool("VG_RECORDER", True)

# --- detection ------------------------------------------------------------
DETECT_MODEL = _env("VG_DETECT_MODEL", "yolo11s.pt")
DETECT_IMGSZ = _int("VG_DETECT_IMGSZ", 640)
DETECT_MAX_BATCH = _int("VG_DETECT_MAX_BATCH", 16)
DETECT_HALF = _bool("VG_DETECT_HALF", True)
DETECT_MIN_CONF = _float("VG_DETECT_MIN_CONF", 0.15)    # floor for detections handed to the tracker
DEVICE = _env("VG_DEVICE", "cuda:0")
TORCH_THREADS = _int("VG_TORCH_THREADS", 4)

# --- tracking -------------------------------------------------------------
TRACK_CONFIRM_HITS = _int("VG_TRACK_CONFIRM_HITS", 3)
TRACK_END_S = _float("VG_TRACK_END_S", 2.0)
TRACK_UPDATE_MIN_S = _float("VG_TRACK_UPDATE_MIN_S", 1.0)

# --- identification -------------------------------------------------------
FACE_MODEL = _env("VG_FACE_MODEL", "buffalo_l")
FACE_MIN_DET = _float("VG_FACE_MIN_DET", 0.6)
FACE_MIN_SIZE = _int("VG_FACE_MIN_SIZE", 36)            # px (shorter side of face box)
FACE_MAX_IDENTIFY = _int("VG_FACE_MAX_IDENTIFY", 3)     # identify calls per track
FACE_INTERVAL_S = _float("VG_FACE_INTERVAL_S", 0.4)     # per track
PLATE_DETECTOR = _env("VG_PLATE_DETECTOR", "yolo-v9-t-384-license-plate-end2end")
PLATE_OCR = _env("VG_PLATE_OCR", "cct-xs-v2-global-model")
PLATE_MIN_CONF = _float("VG_PLATE_MIN_CONF", 0.5)
PLATE_INTERVAL_S = _float("VG_PLATE_INTERVAL_S", 0.4)
PET_MODEL = _env("VG_PET_MODEL", "vit_small_patch14_dinov2.lvd142m")
PET_DELAY_S = _float("VG_PET_DELAY_S", 1.5)             # identify pets after this track age (or at end)

# --- inventory (Gegenstände, langsame Pipeline auf ausgewählten Kameras) ---------------------
GO2RTC_URL = _env("VG_GO2RTC_URL", "http://go2rtc:1984").rstrip("/")   # Fallback-Quelle für Keyframes
INVENTORY_MODEL = _env("VG_INVENTORY_MODEL", "yoloe-11m-seg.pt")      # YOLOE (Text-Prompts) oder COCO-YOLO
INVENTORY_IMGSZ = _int("VG_INVENTORY_IMGSZ", 1280)                     # Gesamtbild
INVENTORY_TILES = _env("VG_INVENTORY_TILES", "2x2")                    # Kacheln "SxZ" (Spalten x Zeilen), "0" = aus
INVENTORY_TILE_IMGSZ = _int("VG_INVENTORY_TILE_IMGSZ", 960)            # Auflösung je Kachel
INVENTORY_OVERLAP = _float("VG_INVENTORY_OVERLAP", 0.15)               # Überlappung der Kacheln
INVENTORY_MAX_BUSY = _float("VG_INVENTORY_MAX_BUSY", 0.25)             # max. GPU-Zeitanteil der Inventar-Pipeline
INVENTORY_HALF = _bool("VG_INVENTORY_HALF", True)
INVENTORY_MIN_CONF = _float("VG_INVENTORY_MIN_CONF", 0.05)             # Untergrenze für predict(conf=…)
INVENTORY_SOURCE = _env("VG_INVENTORY_SOURCE", "auto").lower()        # auto | buffer | go2rtc | rtsp
INVENTORY_CROP_MIN_S = _float("VG_INVENTORY_CROP_MIN_S", 60.0)         # neuer Crop je Gegenstand höchstens alle n s
INVENTORY_UNLOAD_S = _float("VG_INVENTORY_UNLOAD_S", 600.0)            # Modell entladen, wenn so lange keine Kamera aktiv
INVENTORY_PERSON_HOLD_S = _float("VG_INVENTORY_PERSON_HOLD_S", 3.0)    # Personen so lange als Verdeckung werten

# --- misc -----------------------------------------------------------------
STATUS_INTERVAL_S = _float("VG_STATUS_INTERVAL_S", 5.0)
CONFIG_REFRESH_S = _float("VG_CONFIG_REFRESH_S", 60.0)
LOG_LEVEL = _env("VG_LOG_LEVEL", "INFO")
JPEG_QUALITY = _int("VG_JPEG_QUALITY", 88)
