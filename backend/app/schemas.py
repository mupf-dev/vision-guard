"""Pydantic-v2-Schemas (Eingaben) und Serialisierer (Ausgaben) gemäß CONTRACT.md."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


Action = ActionSnapshot | ActionClip | ActionMqtt | ActionWebhook


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    camera_ids: list[int] = Field(default_factory=list)
    zone_ids: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    min_score: float = Field(0.0, ge=0, le=1)
    identity: RuleIdentity = Field(default_factory=RuleIdentity)
    trigger: Literal["enter", "leave", "dwell"] = "enter"
    min_dwell_s: float = Field(0, ge=0)
    schedule: Optional[Schedule] = None
    cooldown_s: float = Field(30, ge=0)
    actions: list[Action] = Field(default_factory=list)

    @field_validator("actions", mode="before")
    @classmethod
    def _actions(cls, v: Any) -> Any:
        return v or []


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


# ---------------------------------------------------------------- Identitäten


class IdentityPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = None
    learning_locked: Optional[bool] = None


class MergeIn(BaseModel):
    into_id: int
    # None = klassischer Merge (alle Samples + Events, Quelle wird gelöscht); sonst Auswahl aus der Quelle
    sample_ids: Optional[list[int]] = Field(None, max_length=1000)


class IdentifyIn(BaseModel):
    kind: Literal["face", "pet", "plate"]
    camera_id: Optional[int] = None
    track_id: Optional[int] = None
    embedding: Optional[list[float]] = None
    plate_text: Optional[str] = None
    crop_path: Optional[str] = None
    quality: float = 0.0


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
