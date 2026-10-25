"""Pydantic-v2-Schemas (Eingaben) und Serialisierer (Ausgaben) gemäß CONTRACT.md."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import DEFAULT_INVENTORY_VOCABULARY, DEFAULT_LABELS

# ---------------------------------------------------------------- Auth


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str


# ---------------------------------------------------------------- Kamera


class Zone(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1, max_length=64)
    name: str = ""
    color: str = "#ff9800"
    polygon: list[list[float]] = Field(default_factory=list)
    # W2 (C3): Zweck der Zone fürs Haustier-Tagebuch
    purpose: Optional[Literal["food", "water", "litter"]] = None

    @field_validator("purpose", mode="before")
    @classmethod
    def _purpose(cls, v: Any) -> Any:
        return v or None

    @field_validator("polygon")
    @classmethod
    def _poly(cls, v: list[list[float]]) -> list[list[float]]:
        for p in v:
            if len(p) != 2:
                raise ValueError("Polygonpunkte müssen [x,y] sein")
            if not all(-0.001 <= c <= 1.001 for c in p):
                raise ValueError("Koordinaten müssen normiert (0..1) sein")
        if v and len(v) < 3:
            raise ValueError("Polygon braucht mindestens 3 Punkte")
        return v


class InventorySuppress(BaseModel):
    """W2 (D2): Fehlerkennung an dieser Stelle unterdrücken (gleiches Label, IoU ≥ 0,5)."""
    label: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    bbox: list[float] = Field(min_length=4, max_length=4)
    review_id: Optional[int] = None

    @field_validator("bbox")
    @classmethod
    def _bbox(cls, v: list[float]) -> list[float]:
        if not all(-0.001 <= c <= 1.001 for c in v) or v[2] <= v[0] or v[3] <= v[1]:
            raise ValueError("bbox muss normiert [x1,y1,x2,y2] sein")
        return [round(float(c), 4) for c in v]


class CameraIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1)
    detect_url: Optional[str] = None
    enabled: bool = True
    detect_fps: float = Field(5, gt=0, le=30)
    labels: list[str] = Field(default_factory=lambda: list(DEFAULT_LABELS))
    min_score: float = Field(0.5, ge=0, le=1)
    face_recognition: bool = True
    plate_recognition: bool = True
    pet_recognition: bool = True
    zones: list[Zone] = Field(default_factory=list)
    # Raum und Gegenstandserkennung (Inventar); Vokabular-Prüfung der Labels im Router
    room: Optional[str] = Field(None, max_length=100)
    inventory_enabled: bool = False
    inventory_interval_s: float = Field(3, ge=1, le=60)
    inventory_labels: list[str] = Field(default_factory=list)
    inventory_min_score: float = Field(0.35, ge=0.05, le=0.95)
    inventory_tiling: bool = False
    # W1 C5: Kamera-Gesundheit prüfen (PTZ-Kameras: aus)
    health_enabled: bool = True
    # W2: Pose/Gesten (C1/D1), Schwellen je Label und Unterdrückungsflächen (D2)
    pose_enabled: bool = False
    gesture_enabled: bool = False
    inventory_label_min_scores: dict[str, float] = Field(default_factory=dict)
    inventory_suppress: list[InventorySuppress] = Field(default_factory=list, max_length=200)
    # W3: Cloud-Fallback „Frag die Kamera“ (A2), Zeitmaschine (B1)
    vlm_cloud_allowed: bool = False
    history_enabled: bool = False
    history_interval_s: float = Field(30, ge=5, le=600)
    history_tiling: bool = False
    # W4 (C2): Wiedererkennung über Kameras (Kleidung/Statur, nur RAM)
    reid_enabled: bool = False
    # PTZ (ONVIF): Schwenken/Neigen, Patrouille, Verfolgung (CONTRACT „Nachträge: PTZ“)
    ptz_enabled: bool = False
    ptz: "PtzConfig" = Field(default_factory=lambda: PtzConfig())

    @field_validator("inventory_label_min_scores")
    @classmethod
    def _label_scores(cls, v: dict[str, float]) -> dict[str, float]:
        for k, x in v.items():
            if not re.match(r"^[a-z0-9_]{1,40}$", str(k)):
                raise ValueError(f"Ungültiger Gegenstands-Schlüssel: {k}")
            if not 0.05 <= float(x) <= 0.95:
                raise ValueError("Schwelle je Gegenstand muss zwischen 0.05 und 0.95 liegen")
        return {str(k): round(float(x), 3) for k, x in v.items()}

    @field_validator("room")
    @classmethod
    def _room(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() or None if isinstance(v, str) else v

    @field_validator("detect_url")
    @classmethod
    def _empty_none(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() or None if isinstance(v, str) else v

    @field_validator("zones")
    @classmethod
    def _unique_zone_ids(cls, v: list[Zone]) -> list[Zone]:
        ids = [z.id for z in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Zonen-IDs müssen eindeutig sein")
        return v


class CameraTestIn(BaseModel):
    url: str = Field(min_length=1)


class CameraTestOut(BaseModel):
    ok: bool
    width: Optional[int] = None
    height: Optional[int] = None
    codec: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------- Regel


class RuleIdentity(BaseModel):
    mode: Literal["any", "known", "unknown", "specific"] = "any"
    identity_ids: list[int] = Field(default_factory=list)


_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


class Schedule(BaseModel):
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    start: str = "00:00"
    end: str = "23:59"

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("Tage 0..6 (0=Mo)")
        return sorted(set(v))

    @field_validator("start", "end")
    @classmethod
    def _time(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError("Zeit im Format HH:MM")
        return v


class ActionSnapshot(BaseModel):
    type: Literal["snapshot"]


class ActionClip(BaseModel):
    type: Literal["clip"]
    pre_s: float = Field(5, ge=0, le=60)
    post_s: float = Field(10, ge=0, le=300)


class ActionMqtt(BaseModel):
    type: Literal["mqtt"]
    topic: str = Field(min_length=1)
    payload: Optional[str] = None
    retain: bool = False


class ActionWebhook(BaseModel):
    type: Literal["webhook"]
    url: str = Field(min_length=1)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    body: Optional[str] = None


class ActionPush(BaseModel):
    """W1: Push über ein Ziel aus Settings `push_targets` (Zugangsdaten stehen nur in den Settings)."""
    type: Literal["push"]
    target: Optional[str] = Field(None, max_length=40)       # id aus push_targets; None = alle aktiven Ziele
    title: str = Field("{rule}", max_length=200)
    message: str = Field("{camera}: {label}", max_length=1000)
    priority: Literal["low", "default", "high"] = "default"
    image: Literal["snapshot", "crop", "none"] = "snapshot"


class ActionPtz(BaseModel):
    """PTZ: Kamera zu einer gespeicherten Position fahren und dort `hold_s` halten (danach Grundposition)."""
    type: Literal["ptz"]
    camera_id: Optional[int] = None          # None = Kamera des Ereignisses
    preset: Optional[str] = Field(None, max_length=64)   # None = Grundposition
    hold_s: float = Field(30, ge=5, le=3600)


Action = ActionSnapshot | ActionClip | ActionMqtt | ActionWebhook | ActionPush | ActionPtz


# ---------- PTZ (ONVIF) – Kamera-Feld `ptz`
class PtzStep(BaseModel):
    preset: str = Field(min_length=1, max_length=64)
    dwell_s: float = Field(20, ge=3, le=3600)


class PtzPatrol(BaseModel):
    enabled: bool = False
    steps: list[PtzStep] = Field(default_factory=list, max_length=32)
    schedule: Optional[Schedule] = None
    pause_s: float = Field(0, ge=0, le=86400)


class PtzFollow(BaseModel):
    enabled: bool = False
    labels: list[str] = Field(default_factory=lambda: ["person"], min_length=1, max_length=20)
    schedule: Optional[Schedule] = None
    lost_s: float = Field(8, ge=2, le=120)
    max_s: float = Field(300, ge=10, le=3600)
    speed: float = Field(0.5, ge=0.1, le=1)
    latency_s: float = Field(1.4, ge=0.3, le=5)    # Bildverzögerung: erst danach wird nachgesteuert
    tilt: bool = True                              # auch neigen (sonst nur schwenken)


class PtzConfig(BaseModel):
    onvif_port: Optional[int] = Field(None, ge=1, le=65535)
    onvif_user: Optional[str] = Field(None, max_length=100)
    onvif_password: Optional[str] = Field(None, max_length=200)   # "***" = unverändert
    profile_token: Optional[str] = Field(None, max_length=64)
    home_preset: Optional[str] = Field(None, max_length=64)
    return_s: float = Field(60, ge=0, le=3600)
    home_on_start: bool = True
    speed: float = Field(0.5, ge=0.1, le=1)
    invert_pan: bool = False
    invert_tilt: bool = False
    patrol: PtzPatrol = Field(default_factory=PtzPatrol)
    follow: PtzFollow = Field(default_factory=PtzFollow)

    @field_validator("onvif_user", "onvif_password", "profile_token", "home_preset")
    @classmethod
    def _blank_none(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() or None if isinstance(v, str) else v


class PtzMoveIn(BaseModel):
    pan: float = Field(0, ge=-1, le=1)
    tilt: float = Field(0, ge=-1, le=1)
    zoom: float = Field(0, ge=-1, le=1)
    duration_ms: int = Field(500, ge=50, le=3000)


class PtzGotoIn(BaseModel):
    preset: Optional[str] = Field(None, max_length=64)    # None = Grundposition
    hold_s: Optional[float] = Field(None, ge=5, le=3600)


class PtzPresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class PtzPauseIn(BaseModel):
    minutes: int = Field(ge=0, le=1440)


class PtzProbeIn(BaseModel):
    onvif_port: Optional[int] = Field(None, ge=1, le=65535)
    onvif_user: Optional[str] = Field(None, max_length=100)
    onvif_password: Optional[str] = Field(None, max_length=200)


CameraIn.model_rebuild()


# ---------- Regel-Engine v2: Trigger-Optionen und Bedingungen (Registry in rules.py)


class TriggerOptionsNone(BaseModel):
    """enter/leave/dwell: keine Optionen (zusätzliche Felder werden verworfen)."""


class TimeTriggerOptions(BaseModel):
    """trigger=time: Uhrzeit (lokal); Wochentage aus schedule.days (schedule.start/end ohne Wirkung)."""
    at: str

    @field_validator("at")
    @classmethod
    def _at(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError("Zeit im Format HH:MM")
        h, m = v.split(":")
        return f"{int(h):02d}:{m}"


GESTURES = ("arms_up", "arm_left", "arm_right", "arms_cross")
POSTURES = ("lying", "sitting", "standing")


class PostureTriggerOptions(BaseModel):
    """W2 (C1): trigger=posture – Haltung seit min_dwell_s; optional kaum Bewegung, ohne Ausschluss-Zonen."""
    posture: Literal["lying", "sitting", "standing"] = "lying"
    max_motion: Optional[float] = Field(None, ge=0, le=1)
    exclude_zone_ids: list[str] = Field(default_factory=list)


class GestureTriggerOptions(BaseModel):
    """W2 (D1): trigger=gesture – eine der Gesten (vg/internal/gesture)."""
    gestures: list[Literal["arms_up", "arm_left", "arm_right", "arms_cross"]] = Field(min_length=1)

    @field_validator("gestures")
    @classmethod
    def _uniq(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


SENSOR_STATES = ("on", "off", "ok", "low", "out", "unknown")


class SensorTriggerOptions(BaseModel):
    """W3 (A3): trigger=sensor – bestätigter Zustandswechsel eines virtuellen Sensors (Regel ohne Event)."""
    sensor_id: int = Field(ge=1)
    state: Literal["on", "off", "ok", "low", "out", "any"] = "on"


class CondSensor(BaseModel):
    """W3 (A3): Bedingung „Sensor ist …“ (für alle Trigger)."""
    type: Literal["sensor"]
    sensor_id: int = Field(ge=1)
    state: Literal["on", "off", "ok", "low", "out", "unknown"] = "on"


class CondInventory(BaseModel):
    type: Literal["inventory"]
    labels: list[str] = Field(min_length=1)
    state: Literal["present", "absent"] = "present"   # absent = kein passender Gegenstand present
    camera_ids: list[int] = Field(default_factory=list)
    room: Optional[str] = Field(None, max_length=100)
    zone_ids: list[str] = Field(default_factory=list)


class CondInventoryUntouched(BaseModel):
    type: Literal["inventory_untouched"]
    labels: list[str] = Field(min_length=1)
    since: str = "06:00"                               # lokal, heute
    camera_ids: list[int] = Field(default_factory=list)
    zone_ids: list[str] = Field(default_factory=list)

    @field_validator("since")
    @classmethod
    def _since(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError("Zeit im Format HH:MM")
        return v


class CondWeather(BaseModel):
    type: Literal["weather"]
    metric: Literal["rain_probability"] = "rain_probability"
    min: float = Field(60, ge=0, le=100)
    hours: int = Field(12, ge=1, le=48)


class CondTrackCameFrom(BaseModel):
    type: Literal["track_came_from"]
    zone_ids: list[str] = Field(min_length=1)


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    camera_ids: list[int] = Field(default_factory=list)
    zone_ids: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    min_score: float = Field(0.0, ge=0, le=1)
    identity: RuleIdentity = Field(default_factory=RuleIdentity)
    # Name aus rules.TRIGGERS (W1: enter|leave|dwell|time); Optionen je Trigger in trigger_options
    trigger: str = Field("enter", max_length=16)
    trigger_options: dict[str, Any] = Field(default_factory=dict)
    min_dwell_s: float = Field(0, ge=0)
    schedule: Optional[Schedule] = None
    cooldown_s: float = Field(30, ge=0)
    # Zusätzliche Prüfungen (alle müssen erfüllt sein); Typen aus rules.CONDITIONS
    conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    condition_delay_s: float = Field(0, ge=0, le=600)
    actions: list[Action] = Field(default_factory=list)
    # W2 (C1): Wiederholung bis Quittierung (0 = aus; sonst 10..3600 s), repeat_max 0 = unbegrenzt
    repeat_s: float = Field(0, ge=0, le=3600)
    repeat_max: int = Field(0, ge=0, le=20)

    @field_validator("repeat_s")
    @classmethod
    def _repeat(cls, v: float) -> float:
        if 0 < v < 10:
            raise ValueError("repeat_s muss 0 oder mindestens 10 s sein")
        return v

    @field_validator("actions", "conditions", mode="before")
    @classmethod
    def _none_list(cls, v: Any) -> Any:
        return v or []

    @field_validator("trigger_options", mode="before")
    @classmethod
    def _none_dict(cls, v: Any) -> Any:
        return v or {}

    @model_validator(mode="after")
    def _registry(self) -> "RuleIn":
        from .rules import validate_rule_parts  # Registry (zyklischen Import vermeiden)

        self.trigger_options, self.conditions = validate_rule_parts(
            self.trigger, self.trigger_options, self.conditions)
        return self


# ---------------------------------------------------------------- Settings


class InventoryVocabEntry(BaseModel):
    label: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    name_de: str = Field(min_length=1, max_length=60)
    group: str = Field("", max_length=60)
    prompts: list[str] = Field(min_length=1, max_length=8)
    min_score: Optional[float] = Field(None, ge=0.05, le=0.95)

    @field_validator("prompts")
    @classmethod
    def _prompts(cls, v: list[str]) -> list[str]:
        v = [p.strip() for p in v if p and p.strip()]
        if not v:
            raise ValueError("Mindestens ein Prompt nötig")
        if any(len(p) > 60 for p in v):
            raise ValueError("Prompt höchstens 60 Zeichen")
        return v


def _default_vocab() -> list[InventoryVocabEntry]:
    return [InventoryVocabEntry(**e) for e in DEFAULT_INVENTORY_VOCABULARY]


# ---------- W1: Push-Ziele, Benachrichtigungs-Routen, Wetter

PUSH_SECRET_FIELDS = ("token", "topic", "user_key", "app_token")   # R1: Topic ist bei ntfy.sh faktisch ein Passwort
NOTIFY_SOURCES = ("rule", "health", "pet", "review", "report", "sensor")
NOTIFY_ROUTE_SOURCES = ("health", "pet", "review", "report", "sensor")   # rule: Ziel steht in der Aktion push


class PushTarget(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    type: Literal["ntfy", "pushover"]
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    server: Optional[str] = Field(None, max_length=300)   # ntfy: https://ntfy.sh, Pushover: https://api.pushover.net
    topic: Optional[str] = Field(None, max_length=100)    # ntfy (Pflicht)
    token: Optional[str] = Field(None, max_length=300)    # ntfy Access-Token (optional) – maskiert
    user_key: Optional[str] = Field(None, max_length=100)  # Pushover – maskiert
    app_token: Optional[str] = Field(None, max_length=100)  # Pushover – maskiert

    @field_validator("server")
    @classmethod
    def _server(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().rstrip("/")
        if not v:
            return None
        if not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("Server muss mit http:// oder https:// beginnen")
        return v

    @field_validator("topic", "token", "user_key", "app_token")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        return (v.strip() or None) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _required(self) -> "PushTarget":
        if self.type == "ntfy":
            if not self.topic or not re.match(r"^[A-Za-z0-9_-]{1,64}$", self.topic):
                raise ValueError("ntfy braucht ein Topic (Buchstaben, Ziffern, _ und -)")
        elif not (self.user_key and self.app_token):
            raise ValueError("Pushover braucht User-Key und App-Token")
        return self


class WeatherLocation(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)

    @field_validator("lat", "lon")
    @classmethod
    def _round(cls, v: float) -> float:
        return round(float(v), 2)   # Datenschutz: nur gerundete Koordinaten verlassen das Haus


def _default_routes() -> dict[str, list[str]]:
    return {k: [] for k in NOTIFY_ROUTE_SOURCES}


PET_PURPOSES = ("food", "water", "litter")


class ReportChannels(BaseModel):
    """W2 (B3): Versandwege des Tagesberichts (Push läuft über notify_routes.report)."""
    mqtt: bool = True
    webhook_url: Optional[str] = Field(None, max_length=500)
    mail_to: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("webhook_url")
    @classmethod
    def _wh(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("Webhook-URL muss mit http:// oder https:// beginnen")
        return v

    @field_validator("mail_to")
    @classmethod
    def _mail(cls, v: list[str]) -> list[str]:
        out = []
        for m in v:
            m = (m or "").strip()
            if not m:
                continue
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", m):
                raise ValueError(f"Ungültige Mail-Adresse: {m}")
            out.append(m)
        return out


class SettingsModel(BaseModel):
    retention_days_events: int = Field(30, ge=1, le=3650)
    retention_days_media: int = Field(7, ge=1, le=3650)
    face_match_threshold: float = Field(0.45, ge=0, le=1)
    pet_match_threshold: float = Field(0.80, ge=0, le=1)
    plate_max_distance: int = Field(1, ge=0, le=5)
    mqtt_prefix: str = Field("visionguard", min_length=1, max_length=100)
    publish_all_events: bool = True
    # Gegenstände (Inventar)
    inventory_vocabulary: list[InventoryVocabEntry] = Field(default_factory=_default_vocab)
    inventory_synonyms: dict[str, list[str]] = Field(default_factory=dict)
    inventory_confirm_n: int = Field(2, ge=1, le=10)
    inventory_miss_m: int = Field(3, ge=1, le=20)
    retention_days_inventory: int = Field(30, ge=1, le=3650)
    # API-Keys / signierte Medien-Links
    media_link_ttl_h: int = Field(24, ge=1, le=720)
    public_base_url: Optional[str] = Field(None, max_length=300)
    # W1: Hinweise und Push
    push_targets: list[PushTarget] = Field(default_factory=list, max_length=20)
    notify_routes: dict[str, list[str]] = Field(default_factory=_default_routes)
    push_max_per_rule_per_day: int = Field(3, ge=0, le=100)   # 0 = ohne Limit
    weather_location: Optional[WeatherLocation] = None
    retention_days_notifications: int = Field(30, ge=1, le=3650)
    # W1: B2 Veränderungen, C5 Kamera-Gesundheit
    inventory_change_min_s: int = Field(120, ge=0, le=3600)
    health_alert_after_s: int = Field(60, ge=10, le=3600)
    # W2: Exemplare/Rückfragen (B4/D2)
    item_match_threshold: float = Field(0.55, ge=0.2, le=0.95)
    item_min_px: int = Field(90, ge=0, le=1000)
    review_enabled: bool = True
    review_max_per_day: int = Field(10, ge=0, le=100)
    review_max_open: int = Field(20, ge=1, le=200)
    review_expire_h: int = Field(48, ge=1, le=720)
    review_band: float = Field(0.10, ge=0.01, le=0.5)
    review_auto_tune: bool = True
    # W2: Pose und Gesten (C1/D1)
    gesture_hold_s: float = Field(1.0, ge=0.5, le=5)
    fall_default_dwell_s: int = Field(60, ge=5, le=3600)
    fall_track_grace_s: int = Field(30, ge=0, le=300)
    # W2: Haustier-Tagebuch (C3)
    pet_diary_labels: list[str] = Field(default_factory=lambda: ["dog", "cat"], max_length=20)
    pet_diary_min_visit_s: dict[str, float] = Field(default_factory=lambda: {"food": 5, "water": 3, "litter": 10})
    pet_diary_notify: bool = True
    pet_diary_single_pet_fallback: bool = True
    # W2: Tagesbericht (B3)
    report_enabled: bool = False
    report_time: str = "21:00"
    report_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    report_generator: Literal["template", "llm"] = "template"
    report_channels: ReportChannels = Field(default_factory=lambda: ReportChannels())
    # W2: gemeinsamer LLM-Client (aus = "none")
    llm_provider: Literal["none", "openai_compat", "anthropic"] = "none"
    llm_base_url: Optional[str] = Field(None, max_length=300)
    llm_model: Optional[str] = Field(None, max_length=200)
    llm_timeout_s: int = Field(60, ge=5, le=600)
    # W3 (K3): Frag die Kamera (A2), Sensoren/Vorräte (A3+C4), Zeitmaschine (B1)
    vlm_enabled: bool = True
    vlm_model: Literal["moondream2-int4", "smolvlm-500m", "qwen25vl-3b-nf4"] = "moondream2-int4"
    vlm_idle_unload_s: int = Field(120, ge=30, le=3600)
    vlm_need_mb: int = Field(3000, ge=500, le=6000)
    vlm_cloud_model: Optional[str] = Field(None, max_length=200)
    retention_days_ask: int = Field(7, ge=1, le=365)
    retention_days_sensors: int = Field(30, ge=1, le=3650)
    shopping_list_auto: bool = True
    retention_days_history: int = Field(14, ge=1, le=365)
    history_min_change: float = Field(0.05, ge=0, le=0.5)
    history_model: Literal["siglip2-b16-256", "mobileclip2-s0"] = "siglip2-b16-256"
    # W4 (C2): Person über mehrere Kameras (Schwelle aus reidtest, messungen.md §11)
    reid_ttl_min: int = Field(60, ge=5, le=720)
    reid_match_threshold: float = Field(0.70, ge=0.3, le=0.95)
    reid_assign_identity: bool = True

    @field_validator("vlm_cloud_model")
    @classmethod
    def _vlm_cloud_model(cls, v: Optional[str]) -> Optional[str]:
        return (v.strip() or None) if isinstance(v, str) else v

    @field_validator("pet_diary_min_visit_s", mode="before")
    @classmethod
    def _visit_s(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return {"food": 5, "water": 3, "litter": 10}
        unknown = [k for k in v if k not in PET_PURPOSES]
        if unknown:
            raise ValueError(f"Unbekannter Zweck: {', '.join(map(str, unknown))} (erlaubt: food, water, litter)")
        out = {"food": 5.0, "water": 3.0, "litter": 10.0}
        for k, x in v.items():
            x = float(x)
            if not 0 <= x <= 600:
                raise ValueError("Mindestdauer je Zweck 0..600 s")
            out[k] = x
        return out

    @field_validator("report_time")
    @classmethod
    def _report_time(cls, v: str) -> str:
        if not _TIME_RE.match(v or ""):
            raise ValueError("report_time im Format HH:MM")
        h, m = v.split(":")
        return f"{int(h):02d}:{m}"

    @field_validator("report_days")
    @classmethod
    def _report_days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("Tage 0..6 (0=Mo)")
        return sorted(set(v))

    @field_validator("llm_base_url")
    @classmethod
    def _llm_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().rstrip("/")
        if not v:
            return None
        if not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("llm_base_url muss mit http:// oder https:// beginnen")
        return v

    @field_validator("llm_model")
    @classmethod
    def _llm_model(cls, v: Optional[str]) -> Optional[str]:
        return (v.strip() or None) if isinstance(v, str) else v

    @field_validator("push_targets")
    @classmethod
    def _targets_unique(cls, v: list[PushTarget]) -> list[PushTarget]:
        ids = [t.id for t in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Push-Ziel-IDs müssen eindeutig sein")
        return v

    @field_validator("notify_routes", mode="before")
    @classmethod
    def _routes(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return _default_routes()
        unknown = [k for k in v if k not in NOTIFY_ROUTE_SOURCES]
        if unknown:
            raise ValueError(f"Unbekannte Quelle in notify_routes: {', '.join(map(str, unknown))}")
        return {k: [str(x) for x in (v.get(k) or [])] for k in NOTIFY_ROUTE_SOURCES}

    @field_validator("weather_location", mode="before")
    @classmethod
    def _weather_empty(cls, v: Any) -> Any:
        return None if v == {} else v

    @field_validator("inventory_vocabulary")
    @classmethod
    def _vocab_unique(cls, v: list[InventoryVocabEntry]) -> list[InventoryVocabEntry]:
        labels = [e.label for e in v]
        if len(labels) != len(set(labels)):
            raise ValueError("Vokabular-Schlüssel (label) müssen eindeutig sein")
        return v

    @field_validator("public_base_url")
    @classmethod
    def _base_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().rstrip("/")
        if not v:
            return None
        if not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("public_base_url muss mit http:// oder https:// beginnen")
        return v

    @field_validator("mqtt_prefix")
    @classmethod
    def _prefix(cls, v: str) -> str:
        v = v.strip().strip("/")
        if not v or "#" in v or "+" in v:
            raise ValueError("Ungültiger MQTT-Präfix")
        return v


class SettingsPatch(BaseModel):
    """PUT /settings: alle Felder optional (Teilaktualisierung erlaubt)."""
    retention_days_events: Optional[int] = None
    retention_days_media: Optional[int] = None
    face_match_threshold: Optional[float] = None
    pet_match_threshold: Optional[float] = None
    plate_max_distance: Optional[int] = None
    mqtt_prefix: Optional[str] = None
    publish_all_events: Optional[bool] = None
    inventory_vocabulary: Optional[list[InventoryVocabEntry]] = None
    inventory_synonyms: Optional[dict[str, list[str]]] = None
    inventory_confirm_n: Optional[int] = None
    inventory_miss_m: Optional[int] = None
    retention_days_inventory: Optional[int] = None
    media_link_ttl_h: Optional[int] = None
    public_base_url: Optional[str] = None
    # W1 – push_targets: Felder mit "***" behalten den gespeicherten Wert (gleiche id);
    # weather_location: {} setzt auf null zurück (null-Werte ignoriert PUT)
    push_targets: Optional[list[dict[str, Any]]] = None
    notify_routes: Optional[dict[str, list[str]]] = None
    push_max_per_rule_per_day: Optional[int] = None
    weather_location: Optional[dict[str, Any]] = None
    retention_days_notifications: Optional[int] = None
    inventory_change_min_s: Optional[int] = None
    health_alert_after_s: Optional[int] = None
    # W2 – llm_base_url/llm_model: "" setzt auf null zurück (null-Werte ignoriert PUT)
    item_match_threshold: Optional[float] = None
    item_min_px: Optional[int] = None
    review_enabled: Optional[bool] = None
    review_max_per_day: Optional[int] = None
    review_max_open: Optional[int] = None
    review_expire_h: Optional[int] = None
    review_band: Optional[float] = None
    review_auto_tune: Optional[bool] = None
    gesture_hold_s: Optional[float] = None
    fall_default_dwell_s: Optional[int] = None
    fall_track_grace_s: Optional[int] = None
    pet_diary_labels: Optional[list[str]] = None
    pet_diary_min_visit_s: Optional[dict[str, float]] = None
    pet_diary_notify: Optional[bool] = None
    pet_diary_single_pet_fallback: Optional[bool] = None
    report_enabled: Optional[bool] = None
    report_time: Optional[str] = None
    report_days: Optional[list[int]] = None
    report_generator: Optional[str] = None
    report_channels: Optional[dict[str, Any]] = None
    llm_provider: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    llm_timeout_s: Optional[int] = None
    # W3 – vlm_cloud_model: "" setzt auf null zurück
    vlm_enabled: Optional[bool] = None
    vlm_model: Optional[str] = None
    vlm_idle_unload_s: Optional[int] = None
    vlm_need_mb: Optional[int] = None
    vlm_cloud_model: Optional[str] = None
    retention_days_ask: Optional[int] = None
    retention_days_sensors: Optional[int] = None
    shopping_list_auto: Optional[bool] = None
    retention_days_history: Optional[int] = None
    history_min_change: Optional[float] = None
    history_model: Optional[str] = None
    # W4 (C2)
    reid_ttl_min: Optional[int] = None
    reid_match_threshold: Optional[float] = None
    reid_assign_identity: Optional[bool] = None


class PushTestIn(BaseModel):
    """POST /settings/push_test: gespeichertes Ziel (`target` = id) oder ungespeicherte Konfiguration (`config`,
    "***" wird durch den gespeicherten Wert desselben Ziels ersetzt)."""
    target: Optional[str] = Field(None, max_length=40)
    config: Optional[dict[str, Any]] = None
    title: str = Field("VisionGuard – Test", max_length=200)
    message: str = Field("Test-Benachrichtigung: Push funktioniert.", max_length=1000)


class HealthReferenceIn(BaseModel):
    """POST /cameras/{id}/health/reference (C5)."""
    mode: Literal["day", "night", "auto"] = "auto"


# ---------------------------------------------------------------- Identitäten


class IdentityPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = None
    learning_locked: Optional[bool] = None


class MergeIn(BaseModel):
    into_id: int
    # None = klassischer Merge (alle Samples + Events, Quelle wird gelöscht); sonst Auswahl aus der Quelle
    sample_ids: Optional[list[int]] = Field(None, max_length=1000)


class IdentityCreateIn(BaseModel):
    """W2 (B4): POST /identities – nur Exemplare (kind item) werden von Hand angelegt."""
    kind: Literal["item"]
    item_label: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    name: str = Field(min_length=1, max_length=200)


class SampleFromInventoryIn(BaseModel):
    """W2 (B4): POST /identities/{id}/samples/from-inventory."""
    inventory_item_id: int


class ReviewAnswerIn(BaseModel):
    """W2 (D2): POST /review/{id}/answer."""
    answer: Literal["yes", "no", "skip"]
    correct_label: Optional[str] = Field(None, pattern=r"^[a-z0-9_]{1,40}$")
    # Herkunft für answer_source: Browser-Sitzung → "ui", API-Key → "api" bzw. "mcp", wenn hier angegeben
    source: Optional[Literal["mcp", "api"]] = None


class ReportGenerateIn(BaseModel):
    """W2 (B3): POST /reports/generate (Zeitangaben wie B2-since; Default heute 00:00 bis jetzt)."""
    since: Optional[str] = Field(None, max_length=64)
    until: Optional[str] = Field(None, max_length=64)
    deliver: bool = False


class IdentifyIn(BaseModel):
    kind: Literal["face", "pet", "plate", "item"]
    camera_id: Optional[int] = None
    track_id: Optional[int] = None
    embedding: Optional[list[float]] = None
    plate_text: Optional[str] = None
    crop_path: Optional[str] = None
    quality: float = 0.0
    # W2 (B4) bei kind="item": Vokabular-Schlüssel und Worker-Item-ID (statt track_id)
    label: Optional[str] = None
    item_id: Optional[str] = None
    # B4: kürzere Seite des Gegenstands im Kamerabild (px) – unter item_min_px keine automatische Zuordnung
    crop_px: Optional[int] = None


class ReidIn(BaseModel):
    """W4 (C2): POST /api/internal/reid – Embedding eines Personen-Tracks (nur RAM, nie gespeichert)."""
    camera_id: int
    track_id: int
    embedding: list[float] = Field(min_length=64, max_length=2048)
    quality: float = 0.0
    ts: Optional[float] = None


class ReidOut(BaseModel):
    presence_key: str
    identity_id: Optional[int] = None
    score: float
    is_new: bool
    # nur gesetzt, wenn die Identität über ReID weitergegeben wird (score ≥ Schwelle + 0,1, Track ohne eigene)
    identity: Optional[dict[str, Any]] = None
    identity_source: Optional[str] = None


class IdentifyOut(BaseModel):
    identity_id: Optional[int]
    display_name: Optional[str]
    name: Optional[str]
    score: float
    is_new: bool


# ---------------------------------------------------------------- Ausgabe-Helfer


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def media_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    if path.startswith("/media/") or path.startswith("http"):
        return path
    return "/media/" + path.lstrip("/")
