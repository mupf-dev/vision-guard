"""Konfiguration aus Umgebungsvariablen."""
import os
from zoneinfo import ZoneInfo

VERSION = "1.0.0"


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


class Config:
    database_url = _env("VG_DATABASE_URL", "postgresql+psycopg://vg:vg@localhost:5432/visionguard")
    mqtt_host = _env("VG_MQTT_HOST", "localhost")
    mqtt_port = int(_env("VG_MQTT_PORT", "1883"))
    go2rtc_url = _env("VG_GO2RTC_URL", "http://localhost:1984").rstrip("/")
    go2rtc_rtsp = _env("VG_GO2RTC_RTSP", "rtsp://localhost:8554").rstrip("/")
    media_dir = _env("VG_MEDIA_DIR", "/data/media")
    admin_user = _env("VG_ADMIN_USER", "admin")
    admin_password = _env("VG_ADMIN_PASSWORD", "admin")
    secret_key = _env("VG_SECRET_KEY", "change-me-please")
    demo_cameras = _env("VG_DEMO_CAMERAS", "0") in ("1", "true", "yes")
    tz_name = _env("TZ", "Europe/Berlin")
    cookie_name = "vg_session"
    session_days = 7

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.tz_name)
        except Exception:
            return ZoneInfo("UTC")


config = Config()

DEFAULT_LABELS = ["person", "car", "truck", "bicycle", "motorcycle", "bus", "dog", "cat", "bird", "horse"]

DEFAULT_SETTINGS = {
    "retention_days_events": 30,
    "retention_days_media": 7,
    "face_match_threshold": 0.45,
    "pet_match_threshold": 0.80,
    "plate_max_distance": 1,
    "mqtt_prefix": "visionguard",
    "publish_all_events": True,
}
