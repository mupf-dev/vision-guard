import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import REAL, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KV(Base):
    """Einfache Key/Value-Ablage: Settings, Zähler, Flags."""
    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    detect_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    detect_fps: Mapped[float] = mapped_column(Float, default=5)
    labels: Mapped[list] = mapped_column(JSONB, default=list)
    min_score: Mapped[float] = mapped_column(Float, default=0.5)
    face_recognition: Mapped[bool] = mapped_column(Boolean, default=True)
    plate_recognition: Mapped[bool] = mapped_column(Boolean, default=True)
    pet_recognition: Mapped[bool] = mapped_column(Boolean, default=True)
    zones: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Inventar (Gegenstände) und Raum – Spalten auch in db.ADDITIVE_COLUMNS
    room: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    inventory_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    inventory_interval_s: Mapped[float] = mapped_column(Float, default=3, server_default=text("3"))
    inventory_labels: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    inventory_min_score: Mapped[float] = mapped_column(Float, default=0.35, server_default=text("0.35"))
    inventory_tiling: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Rule(Base):
    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    camera_ids: Mapped[list] = mapped_column(JSONB, default=list)
    zone_ids: Mapped[list] = mapped_column(JSONB, default=list)
    labels: Mapped[list] = mapped_column(JSONB, default=list)
    min_score: Mapped[float] = mapped_column(Float, default=0.0)
    identity: Mapped[dict] = mapped_column(JSONB, default=dict)
    trigger: Mapped[str] = mapped_column(String(16), default="enter")
    min_dwell_s: Mapped[float] = mapped_column(Float, default=0)
    schedule: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    cooldown_s: Mapped[float] = mapped_column(Float, default=30)
    actions: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Identity(Base):
    __tablename__ = "identities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    auto_number: Mapped[int] = mapped_column(Integer, default=0)
    plate_text: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    plate_norm: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Lernsperre: erkennen ja, keine neuen Samples automatisch speichern
    learning_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    samples: Mapped[list["IdentitySample"]] = relationship(
        back_populates="identity", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.kind == "plate":
            return f"Kennzeichen {self.plate_text or ''}".strip()
        if self.kind == "pet":
            return f"Tier #{self.auto_number}"
        return f"Person #{self.auto_number}"


class IdentitySample(Base):
    __tablename__ = "identity_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identity_id: Mapped[int] = mapped_column(ForeignKey("identities.id", ondelete="CASCADE"), index=True)
    embedding: Mapped[Optional[list[float]]] = mapped_column(ARRAY(REAL), nullable=True)
    plate_text: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    image_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quality: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # per Merge mit Bildauswahl übernommen – wird beim Kürzen zuletzt verdrängt
    manual: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    identity: Mapped[Identity] = relationship(back_populates="samples")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    camera_id: Mapped[int] = mapped_column(Integer, index=True)
    camera_name: Mapped[str] = mapped_column(String(200), default="")
    track_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float] = mapped_column(Float, default=0)
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    zones: Mapped[list] = mapped_column(JSONB, default=list)
    identity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("identities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    thumb_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    snapshot_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clip_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rules: Mapped[list] = mapped_column(JSONB, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    identity: Mapped[Optional[Identity]] = relationship(lazy="joined")

    __table_args__ = (Index("ix_events_cam_track", "camera_id", "track_id"),)


class ApiToken(Base):
    """API-Key für externe Clients (Bearer `vgt_<prefix>_<secret>`); gespeichert wird nur der HMAC-Hash."""
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True)  # "vgt_ab12cd34"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)         # HMAC-SHA256 hex
    scopes: Mapped[list] = mapped_column(JSONB, default=lambda: ["read"])    # ["read"] | ["read","write"]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class InventoryItem(Base):
    """Gegenstandsgedächtnis: eine Zeile je Gegenstand (Kamera + Label + Ort), wird aktualisiert."""
    __tablename__ = "inventory_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(Integer, index=True)
    label: Mapped[str] = mapped_column(String(64), index=True)
    worker_item_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    worker_session: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    prompt: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0)
    bbox: Mapped[list] = mapped_column(JSONB, default=list)        # normiert [x1,y1,x2,y2]
    zones: Mapped[list] = mapped_column(JSONB, default=list)
    state: Mapped[str] = mapped_column(String(16), default="present", index=True)  # present|missing|stale
    relation: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    crop_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    snapshot_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InventoryHistory(Base):
    """Verlauf je Gegenstand: bei Zustandswechsel, sonst höchstens alle 5 min."""
    __tablename__ = "inventory_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True)
    camera_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    state: Mapped[str] = mapped_column(String(16), default="present")
    bbox: Mapped[list] = mapped_column(JSONB, default=list)
    zones: Mapped[list] = mapped_column(JSONB, default=list)
    score: Mapped[float] = mapped_column(Float, default=0)
    crop_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
