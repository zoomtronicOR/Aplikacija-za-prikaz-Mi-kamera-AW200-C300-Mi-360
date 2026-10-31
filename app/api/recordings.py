import asyncio
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.db import get_db
from app.core.security import audit, client_ip, current_user, require_role
from app.models import Camera, Recording, RecordingSettings, RecordingStorage, User
from app.services import smb

router = APIRouter()

HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,255}$")
NAME_RE = re.compile(r"^[^\\/:*?\"<>|]{1,255}$")
CHUNK = 512 * 1024
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


# --- storage -----------------------------------------------------------------------------------

class StorageIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    share: str = Field(min_length=1, max_length=255)
    folder: str = Field(default="", max_length=255)
    username: str = Field(min_length=1, max_length=128)
    password: str | None = Field(default=None, max_length=256)  # None: keep the stored one
    default_retention_days: int = Field(default=14, ge=1, le=3650)
    buffer_max_gb: float = Field(default=5.0, ge=0.5, le=100)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip().lstrip("\\/")
        if not HOST_RE.match(v):
            raise ValueError("unesi samo IP adresu ili ime računara, npr. 192.168.1.10")
        return v

    @field_validator("share")
    @classmethod
    def _share(cls, v: str) -> str:
        v = v.strip().strip("\\/")
        if not NAME_RE.match(v):
            raise ValueError("ime deljenja bez kosih crta, npr. Kamere")
        return v

    @field_validator("folder")
    @classmethod
    def _folder(cls, v: str) -> str:
        v = v.strip().replace("\\", "/").strip("/")
        if ".." in v.split("/"):
            raise ValueError("nedozvoljena putanja")
        return v


def _storage_row(db: Session) -> RecordingStorage:
    st = db.get(RecordingStorage, 1)
    if st is None:
        st = RecordingStorage(id=1)
        db.add(st)
        db.flush()
    return st


def _storage_json(st: RecordingStorage) -> dict:
    return {
        "kind": st.kind, "host": st.host, "share": st.share, "folder": st.folder, "username": st.username,
        "has_password": bool(st.password_enc), "default_retention_days": st.default_retention_days,
        "buffer_max_gb": st.buffer_max_gb, "status_ok": st.status_ok, "status_error": st.status_error,
        "status_at": st.status_at, "pending_bytes": st.pending_bytes,
    }


@router.get("/api/recording/storage")
def get_storage(db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    st = _storage_row(db)
    db.commit()
    return _storage_json(st)


@router.put("/api/recording/storage")
def put_storage(body: StorageIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    st = _storage_row(db)
    st.host, st.share, st.folder, st.username = body.host, body.share, body.folder, body.username
    st.default_retention_days, st.buffer_max_gb = body.default_retention_days, body.buffer_max_gb
    if body.password:
        st.password_enc = encrypt(body.password)
    db.commit()
    audit(db, "recording_storage_set", user=user, target=f"\\\\{body.host}\\{body.share}",
          detail=f"folder={body.folder} user={body.username} password={'changed' if body.password else 'kept'}",
          ip=client_ip(request))
    return _storage_json(st)


@router.post("/api/recording/storage/test")
async def test_storage(body: StorageIn, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    password = body.password
    if not password:
        stored = smb.load(db)
        if stored is None:
            raise HTTPException(400, "unesi lozinku")
        password = stored.password
    share = smb.Share(body.host, body.share, body.folder, body.username, password)
    try:
        await asyncio.to_thread(smb.check, share)
    except smb.StorageError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


# --- per-camera settings -----------------------------------------------------------------------

class CameraRecIn(BaseModel):
    mode: Literal["off", "continuous", "schedule", "motion"]
    schedule_start: time | None = None
    schedule_end: time | None = None
    schedule_days: str = Field(default="0123456", pattern=r"^[0-6]{0,7}$")
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    motion_sensitivity: int = Field(default=50, ge=1, le=100)
    motion_mask: str | None = Field(default=None, pattern=r"^[01]{144}$")  # 9x16 blocks, 1 = ignore
    # switches for the optional features
    motion_auto_ignore: bool = True
    motion_zones_enabled: bool = True
    motion_noise_adapt: bool = True
    motion_require_group: bool = True
    motion_pre_roll: bool = True
    record_audio: bool = True


TOGGLES = ("motion_auto_ignore", "motion_zones_enabled", "motion_noise_adapt", "motion_require_group",
           "motion_pre_roll", "record_audio")


def _settings_for(db: Session, cam: Camera) -> RecordingSettings:
    rs = db.get(RecordingSettings, cam.id)
    if rs is None:
        rs = RecordingSettings(camera_id=cam.id)
        db.add(rs)
        db.flush()
    return rs


def rec_json(rs: RecordingSettings | None) -> dict:
    if rs is None:
        return {"mode": "off", "active": False, "reason": None, "manual": False}
    fresh = rs.status_at is not None and rs.status_at > datetime.now(timezone.utc) - timedelta(seconds=90)
    return {
        "mode": rs.mode, "manual": rs.manual_active, "manual_by": rs.manual_by,
        "schedule_start": rs.schedule_start, "schedule_end": rs.schedule_end, "schedule_days": rs.schedule_days,
        "retention_days": rs.retention_days, "motion_sensitivity": rs.motion_sensitivity,
        "last_motion_at": rs.last_motion_at,
        "motion_level": rs.motion_level if fresh and rs.mode == "motion" else None,
        "motion_mask": rs.motion_mask, **{t: getattr(rs, t) for t in TOGGLES},
        "motion_live": rs.motion_live if fresh and rs.mode == "motion" else None,
        "motion_suppressed": rs.motion_suppressed if fresh and rs.mode == "motion" else None,
        # status older than 90 s means the recorder isn't running
        "active": bool(rs.rec_active and fresh), "reason": rs.rec_reason if fresh else None,
        "recorder_alive": fresh, "error": rs.last_error,
    }


def _camera(db: Session, slug: str) -> Camera:
    cam = db.execute(select(Camera).where(Camera.slug == slug, Camera.enabled.is_(True))).scalar_one_or_none()
    if cam is None:
        raise HTTPException(404, "Kamera ne postoji")
    return cam


@router.get("/api/recording/cameras")
def list_rec_settings(db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    cams = db.execute(select(Camera).where(Camera.enabled.is_(True)).order_by(Camera.sort_order, Camera.name)).scalars()
    return [{"slug": c.slug, "name": c.name, **rec_json(db.get(RecordingSettings, c.id))} for c in cams]


@router.put("/api/recording/cameras/{slug}")
def put_rec_settings(slug: str, body: CameraRecIn, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin"))):
    if body.mode == "schedule" and (body.schedule_start is None or body.schedule_end is None or not body.schedule_days):
        raise HTTPException(400, "Za raspored unesi početak, kraj i bar jedan dan")
    cam = _camera(db, slug)
    rs = _settings_for(db, cam)
    rs.mode, rs.schedule_start, rs.schedule_end = body.mode, body.schedule_start, body.schedule_end
    rs.schedule_days, rs.retention_days = body.schedule_days, body.retention_days
    rs.motion_sensitivity = body.motion_sensitivity
    rs.motion_mask = body.motion_mask if body.motion_mask and "1" in body.motion_mask else None
    for t in TOGGLES:
        setattr(rs, t, getattr(body, t))
    db.commit()
    audit(db, "recording_settings_set", user=user, target=slug, detail=body.model_dump_json(), ip=client_ip(request))
    return rec_json(rs)


class ManualIn(BaseModel):
    active: bool


@router.post("/api/recording/cameras/{slug}/manual")
def manual_record(slug: str, body: ManualIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_role("admin", "user"))):
    cam = _camera(db, slug)
    rs = _settings_for(db, cam)
    rs.manual_active, rs.manual_by = body.active, user.username if body.active else None
    db.commit()
    audit(db, "recording_manual_start" if body.active else "recording_manual_stop", user=user, target=slug,
          ip=client_ip(request))
    return rec_json(rs)


# --- recordings --------------------------------------------------------------------------------

@router.get("/api/recordings")
def list_recordings(camera: str | None = None, day: date | None = None, offset: int = 0, limit: int = 100,
                    db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = select(Recording)
    if camera:
        q = q.where(Recording.camera_slug == camera)
    if day:
        tz = ZoneInfo(get_settings().timezone)
        start = datetime.combine(day, time(0), tz)
        q = q.where(Recording.started_at >= start, Recording.started_at < start + timedelta(days=1))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(Recording.started_at.desc()).offset(max(0, offset)).limit(min(limit, 500))).scalars()
    names = dict(db.execute(select(Camera.slug, Camera.name)).all())
    return {
        "total": total,
        "items": [{
            "id": r.id, "camera": r.camera_slug, "camera_name": names.get(r.camera_slug, r.camera_slug),
            "started_at": r.started_at, "ended_at": r.ended_at,
            "duration_s": round((r.ended_at - r.started_at).total_seconds()), "size_bytes": r.size_bytes,
            "reason": r.reason, "ext": r.path.rsplit(".", 1)[-1],
        } for r in rows],
    }


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    m = RANGE_RE.match(header or "")
    if not m or (not m[1] and not m[2]):
        return None
    if m[1]:
        start, end = int(m[1]), int(m[2]) if m[2] else size - 1
    else:  # suffix range: last N bytes
        start, end = max(0, size - int(m[2])), size - 1
    if start >= size or start > end:
        raise HTTPException(416, "Neispravan opseg")
    return start, min(end, size - 1)


@router.get("/api/recordings/{rec_id}/file")
async def recording_file(rec_id: int, download: bool = False, range: str | None = Header(default=None),
                         db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Streams a recording from the share, with Range support so the browser can seek."""
    if download and user.role not in ("admin", "user"):
        raise HTTPException(403, "Nemate dozvolu za preuzimanje")
    rec = db.get(Recording, rec_id)
    if rec is None:
        raise HTTPException(404, "Snimak ne postoji")
    share = smb.load(db)
    if share is None:
        raise HTTPException(503, "Skladište nije podešeno")
    size, ext = rec.size_bytes, rec.path.rsplit(".", 1)[-1]
    span = _parse_range(range, size)
    start, end = span or (0, size - 1)

    try:
        f = await asyncio.to_thread(smb.open_read, share, rec.path)
    except smb.StorageError as e:
        raise HTTPException(502, f"Snimak nije dostupan: {e}")

    async def body():
        try:
            await asyncio.to_thread(f.seek, start)
            left = end - start + 1
            while left > 0:
                chunk = await asyncio.to_thread(f.read, min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk
        finally:
            await asyncio.to_thread(f.close)

    name = f"{rec.camera_slug}_{rec.started_at.astimezone(ZoneInfo(get_settings().timezone)):%Y-%m-%d_%H-%M-%S}.{ext}"
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)}
    if span:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    media = "video/mp4" if ext == "mp4" else "video/x-matroska"
    return StreamingResponse(body(), status_code=206 if span else 200, media_type=media, headers=headers)


@router.delete("/api/recordings/{rec_id}", status_code=204)
async def delete_recording(rec_id: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require_role("admin"))):
    rec = db.get(Recording, rec_id)
    if rec is None:
        raise HTTPException(404, "Snimak ne postoji")
    share = smb.load(db)
    if share is not None:
        try:
            await asyncio.to_thread(smb.delete, share, rec.path)
        except smb.StorageError as e:
            raise HTTPException(502, f"Brisanje nije uspelo: {e}")
    db.delete(rec)
    db.commit()
    audit(db, "recording_deleted", user=user, target=rec.camera_slug, detail=rec.path, ip=client_ip(request))
