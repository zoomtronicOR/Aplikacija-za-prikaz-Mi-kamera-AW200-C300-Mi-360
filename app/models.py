from datetime import datetime, time

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, Time,
                        func)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ROLES = ("admin", "user", "viewer")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(primary_key=True)
    # slug doubles as the go2rtc stream name
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    location: Mapped[str | None] = mapped_column(String(128))
    icon: Mapped[str | None] = mapped_column(String(32))
    # "xiaomi": imported from go2rtc, miIO controls; "generic": any go2rtc-supported URL, added in the UI
    kind: Mapped[str] = mapped_column(String(16), default="xiaomi")
    source_display: Mapped[str | None] = mapped_column(String(512))  # password masked
    model: Mapped[str | None] = mapped_column(String(64))
    did: Mapped[str | None] = mapped_column(String(32), unique=True)
    ip: Mapped[str | None] = mapped_column(String(64))
    mac: Mapped[str | None] = mapped_column(String(32))
    region: Mapped[str] = mapped_column(String(8), default="de")
    stream_profile: Mapped[str | None] = mapped_column(String(16))
    # separate go2rtc stream for the SD profile; the cameras accept a second session alongside HD
    sd_stream: Mapped[str | None] = mapped_column(String(64))
    # controls derived from the MIoT spec for models the app doesn't know (added through the UI)
    controls_json: Mapped[str | None] = mapped_column(Text)
    ptz: Mapped[bool | None] = mapped_column(Boolean)  # None: the model's default
    notify_motion: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


REC_MODES = ("off", "continuous", "schedule", "motion")


class RecordingStorage(Base):
    """Where finished clips go. A single row (id=1); the recorder writes the status fields."""
    __tablename__ = "recording_storage"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), default="smb")
    host: Mapped[str | None] = mapped_column(String(255))
    share: Mapped[str | None] = mapped_column(String(255))
    folder: Mapped[str | None] = mapped_column(String(255))  # path inside the share
    username: Mapped[str | None] = mapped_column(String(128))
    password_enc: Mapped[str | None] = mapped_column(Text)  # Fernet, key in .env
    default_retention_days: Mapped[int] = mapped_column(Integer, default=14)
    buffer_max_gb: Mapped[float] = mapped_column(Float, default=5.0)  # local queue cap while the share is down
    # written by the recorder
    status_ok: Mapped[bool | None] = mapped_column(Boolean)
    status_error: Mapped[str | None] = mapped_column(Text)
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class RecordingSettings(Base):
    __tablename__ = "recording_settings"

    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="off")
    schedule_start: Mapped[time | None] = mapped_column(Time)
    schedule_end: Mapped[time | None] = mapped_column(Time)  # may be before start: window crosses midnight
    schedule_days: Mapped[str] = mapped_column(String(7), default="0123456")  # Monday=0
    retention_days: Mapped[int | None] = mapped_column(Integer)  # None: storage default
    manual_active: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_by: Mapped[str | None] = mapped_column(String(64))
    motion_sensitivity: Mapped[int] = mapped_column(Integer, default=50)
    # written by the recorder
    rec_active: Mapped[bool] = mapped_column(Boolean, default=False)
    rec_reason: Mapped[str | None] = mapped_column(String(16))
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_motion_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    motion_level: Mapped[float | None] = mapped_column(Float)
    # 144-char '0'/'1' block masks (9 rows x 16 columns), see migration 0006
    motion_mask: Mapped[str | None] = mapped_column(String(144))
    # every optional recording feature has its own switch (user requirement)
    motion_auto_ignore: Mapped[bool] = mapped_column(Boolean, default=True)
    motion_zones_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    motion_noise_adapt: Mapped[bool] = mapped_column(Boolean, default=True)
    motion_require_group: Mapped[bool] = mapped_column(Boolean, default=True)
    motion_pre_roll: Mapped[bool] = mapped_column(Boolean, default=True)
    record_audio: Mapped[bool] = mapped_column(Boolean, default=True)
    # AI verification of motion: off by default; each object group has its own switch
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_person: Mapped[bool] = mapped_column(Boolean, default=True)
    ai_vehicle: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_animal: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_min_score: Mapped[float] = mapped_column(Float, default=0.5)
    ai_last: Mapped[str | None] = mapped_column(String(64))
    ai_last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    motion_live: Mapped[str | None] = mapped_column(String(144))
    motion_suppressed: Mapped[str | None] = mapped_column(String(144))


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id", ondelete="SET NULL"), index=True)
    camera_slug: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    path: Mapped[str] = mapped_column(String(512))  # relative to the storage folder
    reason: Mapped[str] = mapped_column(String(16))  # manual | continuous | schedule | motion
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    """Something that happened (motion, camera down/up, storage trouble); the notifier sends it."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(Text)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    thumb: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)  # JPEG, loaded only when asked


class NotifyChannel(Base):
    __tablename__ = "notify_channels"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[str] = mapped_column(Text, default="{}")
    secret_enc: Mapped[str | None] = mapped_column(Text)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class NotifySettings(Base):
    """Single row (id=1): which events notify, and the rules every optional behaviour has a switch for."""
    __tablename__ = "notify_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    on_motion: Mapped[bool] = mapped_column(Boolean, default=True)
    on_camera_offline: Mapped[bool] = mapped_column(Boolean, default=True)
    on_camera_online: Mapped[bool] = mapped_column(Boolean, default=True)
    on_storage: Mapped[bool] = mapped_column(Boolean, default=True)
    with_snapshot: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=5)
    quiet_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_start: Mapped[time | None] = mapped_column(Time)
    quiet_end: Mapped[time | None] = mapped_column(Time)
    # event archive
    event_thumbs: Mapped[bool] = mapped_column(Boolean, default=True)
    events_keep_days: Mapped[int] = mapped_column(Integer, default=30)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    username: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
