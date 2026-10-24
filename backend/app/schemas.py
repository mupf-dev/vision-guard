"""Pydantic-v2-Schemas (Eingaben) und Serialisierer (Ausgaben) gemäß CONTRACT.md."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import DEFAULT_LABELS

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


class SettingsModel(BaseModel):
    retention_days_events: int = Field(30, ge=1, le=3650)
    retention_days_media: int = Field(7, ge=1, le=3650)
    face_match_threshold: float = Field(0.45, ge=0, le=1)
    pet_match_threshold: float = Field(0.80, ge=0, le=1)
    plate_max_distance: int = Field(1, ge=0, le=5)
    mqtt_prefix: str = Field("visionguard", min_length=1, max_length=100)
    publish_all_events: bool = True

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


# ---------------------------------------------------------------- Identitäten


class IdentityPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = None


class MergeIn(BaseModel):
    into_id: int


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
