"""Moves finished clips to the SMB share, indexes them in `recordings`, and applies retention."""
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Camera, Recording, RecordingSettings, RecordingStorage
from app.services import smb

log = logging.getLogger(__name__)

CLIP_RE = re.compile(r"^(?P<slug>[\w-]+)__(?P<start>\d{8}T\d{6}Z)__(?P<end>\d{8}T\d{6}Z)__(?P<reason>\w+)\.(?P<ext>mp4|mkv)$")
RETENTION_BATCH = 200


def pending_clips(clip_dir: Path) -> list[Path]:
    if not clip_dir.exists():
        return []
    return sorted(p for p in clip_dir.iterdir() if CLIP_RE.match(p.name))


def buffer_bytes(buffer_dir: Path) -> int:
    return sum(p.stat().st_size for p in buffer_dir.rglob("*") if p.is_file()) if buffer_dir.exists() else 0


def _ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def upload_pending(db: Session, share: smb.Share, clip_dir: Path, tz: ZoneInfo) -> int:
    """Uploads clips oldest first. Stops at the first storage error (raised to the caller)."""
    done = 0
    for clip in pending_clips(clip_dir):
        m = CLIP_RE.match(clip.name)
        start, end = _ts(m["start"]), _ts(m["end"])
        local = start.astimezone(tz)
        rel = f"{m['slug']}/{local:%Y-%m-%d}/{local:%H-%M-%S}_{m['reason']}.{m['ext']}"
        size = clip.stat().st_size
        smb.upload(share, str(clip), rel)
        cam = db.execute(select(Camera).where(Camera.slug == m["slug"])).scalar_one_or_none()
        db.add(Recording(camera_id=cam.id if cam else None, camera_slug=m["slug"], started_at=start, ended_at=end,
                         size_bytes=size, path=rel, reason=m["reason"]))
        db.commit()
        clip.unlink()
        done += 1
        log.info("uploaded %s", rel)
    return done


def enforce_buffer_cap(clip_dir: Path, max_bytes: int) -> list[str]:
    """While the share is unreachable clips pile up locally; drop the oldest beyond the cap."""
    dropped = []
    clips = pending_clips(clip_dir)
    total = sum(c.stat().st_size for c in clips)
    while clips and total > max_bytes:
        c = clips.pop(0)
        total -= c.stat().st_size
        c.unlink()
        dropped.append(c.name)
    if dropped:
        log.warning("buffer full, dropped %d clip(s): %s", len(dropped), ", ".join(dropped))
    return dropped


def apply_retention(db: Session, share: smb.Share) -> int:
    st = db.get(RecordingStorage, 1)
    default_days = st.default_retention_days if st else 14
    days_by_cam = {cid: d for cid, d in db.execute(select(RecordingSettings.camera_id, RecordingSettings.retention_days))}
    now = datetime.now(timezone.utc)
    removed = 0
    oldest_first = select(Recording).order_by(Recording.started_at).limit(RETENTION_BATCH * 5)
    for rec in db.execute(oldest_first).scalars():
        days = days_by_cam.get(rec.camera_id) or default_days
        if rec.started_at >= now - timedelta(days=days):
            continue
        smb.delete(share, rec.path)
        db.delete(rec)
        db.commit()
        removed += 1
        if removed >= RETENTION_BATCH:
            break
    if removed:
        log.info("retention removed %d recording(s)", removed)
    return removed
