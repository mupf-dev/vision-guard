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
    # Welle 2: Schlüssel für den LLM-Client – nur aus der Env, nie in Settings/API/Logs
    llm_api_key = os.getenv("VG_LLM_API_KEY") or ""
    anthropic_api_key = os.getenv("VG_ANTHROPIC_API_KEY") or ""
    # Welle 3 (A2): interner VLM-Dienst (Compose-Profil vlm)
    vlm_url = _env("VG_VLM_URL", "http://vlm:8200").rstrip("/")
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

DEFAULT_SECRET_KEYS = {"change-me-please", "change-me", "changeme", "secret", ""}
WEAK_PASSWORDS = {"admin", "password", "passwort", "123456", "12345678", "visionguard", "changeme", "change-me"}
MIN_SECRET_LEN = 32
MIN_PASSWORD_LEN = 8


def secret_key_problem() -> str | None:
    """Grund, warum ``VG_SECRET_KEY`` unsicher ist (Standardwert/zu kurz), sonst None.

    Mit einem bekannten Schlüssel ließen sich Sitzungs-JWTs und Medien-Signaturen fälschen – das Backend startet
    dann nicht (API und MCP sind von außen erreichbar)."""
    k = config.secret_key
    if k in DEFAULT_SECRET_KEYS:
        return "VG_SECRET_KEY ist der Standardwert"
    if len(k) < MIN_SECRET_LEN:
        return f"VG_SECRET_KEY ist kürzer als {MIN_SECRET_LEN} Zeichen"
    return None


def weak_admin_password() -> bool:
    """Schwaches Admin-Passwort → Anmeldung nur aus privaten Netzen (siehe ``auth.login_allowed_from``)."""
    p = config.admin_password
    return (p.lower() in WEAK_PASSWORDS or len(p) < MIN_PASSWORD_LEN
            or p.lower() == config.admin_user.lower())

DEFAULT_LABELS = ["person", "car", "truck", "bicycle", "motorcycle", "bus", "dog", "cat", "bird", "horse"]


# Standard-Vokabular der Gegenstandserkennung (Quelle: docs/plan/vokabular-50.md).
# label = stabiler englischer Schlüssel, name_de = Anzeige, prompts = englische Texte fürs Modell,
# min_score = None -> inventory_min_score der Kamera.
DEFAULT_INVENTORY_VOCABULARY: list[dict] = [
    {"label": "keys", "name_de": "Schlüssel", "group": "Persönliches", "prompts": ["keys", "key ring"], "min_score": None},
    {"label": "wallet", "name_de": "Geldbörse", "group": "Persönliches", "prompts": ["wallet", "purse"], "min_score": None},
    {"label": "phone", "name_de": "Handy", "group": "Persönliches", "prompts": ["cell phone", "smartphone"], "min_score": None},
    {"label": "eyeglasses", "name_de": "Brille", "group": "Persönliches", "prompts": ["eyeglasses"], "min_score": None},
    {"label": "sunglasses", "name_de": "Sonnenbrille", "group": "Persönliches", "prompts": ["sunglasses"], "min_score": None},
    {"label": "wristwatch", "name_de": "Armbanduhr", "group": "Persönliches", "prompts": ["wristwatch"], "min_score": None},
    {"label": "headphones", "name_de": "Kopfhörer", "group": "Persönliches", "prompts": ["headphones", "earbuds case"], "min_score": None},
    {"label": "backpack", "name_de": "Rucksack", "group": "Persönliches", "prompts": ["backpack"], "min_score": None},
    {"label": "handbag", "name_de": "Handtasche", "group": "Persönliches", "prompts": ["handbag"], "min_score": None},
    {"label": "umbrella", "name_de": "Regenschirm", "group": "Persönliches", "prompts": ["umbrella"], "min_score": None},
    {"label": "medicine", "name_de": "Medikamente", "group": "Persönliches", "prompts": ["pill bottle", "medicine box"], "min_score": None},
    {"label": "jacket", "name_de": "Jacke", "group": "Persönliches", "prompts": ["jacket", "coat"], "min_score": None},
    {"label": "remote", "name_de": "Fernbedienung", "group": "Elektronik", "prompts": ["remote control"], "min_score": None},
    {"label": "laptop", "name_de": "Laptop", "group": "Elektronik", "prompts": ["laptop"], "min_score": None},
    {"label": "tablet", "name_de": "Tablet", "group": "Elektronik", "prompts": ["tablet computer"], "min_score": None},
    {"label": "charger", "name_de": "Ladekabel", "group": "Elektronik", "prompts": ["charger", "charging cable"], "min_score": None},
    {"label": "game_controller", "name_de": "Spielecontroller", "group": "Elektronik", "prompts": ["game controller"], "min_score": None},
    {"label": "tv", "name_de": "Fernseher", "group": "Elektronik", "prompts": ["television"], "min_score": None},
    {"label": "flashlight", "name_de": "Taschenlampe", "group": "Elektronik", "prompts": ["flashlight"], "min_score": None},
    {"label": "mug", "name_de": "Tasse", "group": "Küche", "prompts": ["mug", "cup"], "min_score": None},
    {"label": "glass", "name_de": "Glas", "group": "Küche", "prompts": ["drinking glass"], "min_score": None},
    {"label": "bottle", "name_de": "Flasche", "group": "Küche", "prompts": ["bottle"], "min_score": None},
    {"label": "plate", "name_de": "Teller", "group": "Küche", "prompts": ["plate"], "min_score": None},
    {"label": "bowl", "name_de": "Schüssel", "group": "Küche", "prompts": ["bowl"], "min_score": None},
    {"label": "fork", "name_de": "Gabel", "group": "Küche", "prompts": ["fork"], "min_score": None},
    {"label": "spoon", "name_de": "Löffel", "group": "Küche", "prompts": ["spoon"], "min_score": None},
    {"label": "knife", "name_de": "Messer", "group": "Küche", "prompts": ["knife"], "min_score": None},
    {"label": "pot", "name_de": "Topf", "group": "Küche", "prompts": ["cooking pot"], "min_score": None},
    {"label": "pan", "name_de": "Pfanne", "group": "Küche", "prompts": ["frying pan"], "min_score": None},
    {"label": "cutting_board", "name_de": "Schneidebrett", "group": "Küche", "prompts": ["cutting board"], "min_score": None},
    {"label": "kettle", "name_de": "Wasserkocher", "group": "Küche", "prompts": ["electric kettle"], "min_score": None},
    {"label": "table", "name_de": "Tisch", "group": "Möbel & Einrichtung", "prompts": ["table", "dining table"], "min_score": None},
    {"label": "chair", "name_de": "Stuhl", "group": "Möbel & Einrichtung", "prompts": ["chair"], "min_score": None},
    {"label": "sofa", "name_de": "Sofa", "group": "Möbel & Einrichtung", "prompts": ["sofa", "couch"], "min_score": None},
    {"label": "bed", "name_de": "Bett", "group": "Möbel & Einrichtung", "prompts": ["bed"], "min_score": None},
    {"label": "rug", "name_de": "Teppich", "group": "Möbel & Einrichtung", "prompts": ["rug", "carpet"], "min_score": None},
    {"label": "pillow", "name_de": "Kissen", "group": "Möbel & Einrichtung", "prompts": ["pillow", "cushion"], "min_score": None},
    {"label": "blanket", "name_de": "Decke", "group": "Möbel & Einrichtung", "prompts": ["blanket"], "min_score": None},
    {"label": "lamp", "name_de": "Lampe", "group": "Möbel & Einrichtung", "prompts": ["lamp"], "min_score": None},
    {"label": "potted_plant", "name_de": "Zimmerpflanze", "group": "Möbel & Einrichtung", "prompts": ["potted plant"], "min_score": None},
    {"label": "book", "name_de": "Buch", "group": "Sonstiges", "prompts": ["book"], "min_score": None},
    {"label": "pen", "name_de": "Stift", "group": "Sonstiges", "prompts": ["pen"], "min_score": None},
    {"label": "scissors", "name_de": "Schere", "group": "Sonstiges", "prompts": ["scissors"], "min_score": None},
    {"label": "tool", "name_de": "Werkzeug", "group": "Sonstiges", "prompts": ["hammer", "screwdriver"], "min_score": None},
    {"label": "parcel", "name_de": "Paket", "group": "Sonstiges", "prompts": ["cardboard box", "parcel"], "min_score": None},
    {"label": "toy", "name_de": "Spielzeug", "group": "Sonstiges", "prompts": ["toy", "stuffed animal"], "min_score": None},
    {"label": "ball", "name_de": "Ball", "group": "Sonstiges", "prompts": ["ball"], "min_score": None},
    {"label": "shoes", "name_de": "Schuhe", "group": "Sonstiges", "prompts": ["shoes"], "min_score": None},
    {"label": "helmet", "name_de": "Fahrradhelm", "group": "Sonstiges", "prompts": ["helmet"], "min_score": None},
    {"label": "trash_can", "name_de": "Mülleimer", "group": "Sonstiges", "prompts": ["trash can"], "min_score": None},
]


DEFAULT_SETTINGS = {
    "retention_days_events": 30,
    "retention_days_media": 7,
    "face_match_threshold": 0.45,
    "pet_match_threshold": 0.80,
    "plate_max_distance": 1,
    "mqtt_prefix": "visionguard",
    "publish_all_events": True,
    # Gegenstände (Inventar)
    "inventory_vocabulary": DEFAULT_INVENTORY_VOCABULARY,
    "inventory_synonyms": {},
    # W0-Messung (A1-Flackertest): 3/10 statt 2/3 senkt present↔missing-Wechsel deutlich
    "inventory_confirm_n": 3,
    "inventory_miss_m": 10,
    "retention_days_inventory": 30,
    # API-Keys / signierte Medien-Links
    "media_link_ttl_h": 24,
    "public_base_url": None,
    # Welle 1: Hinweise, Push, Veränderungen, Kamera-Gesundheit
    "push_targets": [],
    "notify_routes": {"health": [], "pet": [], "review": [], "report": [], "sensor": []},
    "push_max_per_rule_per_day": 3,
    "weather_location": None,
    "retention_days_notifications": 30,
    "inventory_change_min_s": 120,
    "health_alert_after_s": 60,
    # Welle 2 (K2): Exemplare/Rückfragen (B4/D2, Werte aus messungen.md §3)
    "item_match_threshold": 0.55,
    "item_min_px": 90,
    "review_enabled": True,
    "review_max_per_day": 10,
    "review_max_open": 20,
    "review_expire_h": 48,
    "review_band": 0.10,
    "review_auto_tune": True,
    # Pose und Gesten (C1/D1)
    "gesture_hold_s": 1.0,
    "fall_default_dwell_s": 60,
    "fall_track_grace_s": 30,
    # Haustier-Tagebuch (C3)
    "pet_diary_labels": ["dog", "cat"],
    "pet_diary_min_visit_s": {"food": 5, "water": 3, "litter": 10},
    "pet_diary_notify": True,
    "pet_diary_single_pet_fallback": True,
    # Tagesbericht (B3)
    "report_enabled": False,
    "report_time": "21:00",
    "report_days": [0, 1, 2, 3, 4, 5, 6],
    "report_generator": "template",
    "report_channels": {"mqtt": True, "webhook_url": None, "mail_to": []},
    # Gemeinsamer LLM-Client (standardmäßig aus; Schlüssel nur per Env VG_LLM_API_KEY / VG_ANTHROPIC_API_KEY)
    "llm_provider": "none",
    "llm_base_url": None,
    "llm_model": None,
    "llm_timeout_s": 60,
    # Welle 3 (K3): Frag die Kamera (A2, Werte aus messungen.md §6) – lokales VLM nur mit Compose-Profil vlm
    "vlm_enabled": True,
    "vlm_model": "moondream2-int4",
    "vlm_idle_unload_s": 120,
    "vlm_need_mb": 3000,
    "vlm_cloud_model": None,
    "retention_days_ask": 7,
    # Virtuelle Sensoren und Vorräte (A3+C4)
    "retention_days_sensors": 30,
    "shopping_list_auto": True,
    # Zeitmaschine (B1, messungen.md §5)
    "retention_days_history": 14,
    "history_min_change": 0.05,
    "history_model": "siglip2-b16-256",
    # Welle 4 (C2): Person über mehrere Kameras – Galerie nur im RAM (reidtest, messungen.md §11)
    "reid_ttl_min": 60,
    "reid_match_threshold": 0.70,
    "reid_assign_identity": True,
    # Identitäten 07 (CONTRACT ID.E): Gesichtsqualität/Zuordnung (Pakete B/C)
    "face_new_min_quality": 0.35,
    "face_new_min_size": 80,
    "face_sample_min_quality": 0.30,
    "face_match_margin": 0.05,
    "face_max_samples_named": 40,
    "face_track_faces": 5,
    "face_fullres": True,   # Paket B: Machbarkeitstest im Rahmen (docs/plan/07-identitaeten-fortschritt.md)
    # Nachtlauf (Paket D) – Standard Probelauf (Entscheidung des Nutzers: erst Vorschau ansehen)
    "nightly_mode": "preview",
    "nightly_time": "03:00",
    "nightly_auto_merge_min": 0.55,
    "nightly_auto_merge_margin": 0.08,
    "nightly_review_min": 0.40,
    "nightly_review_max_per_day": 5,
    "nightly_curate": True,
    "nightly_thresholds": True,
    "nightly_cleanup_days": 3,
}
