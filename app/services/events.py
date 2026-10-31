"""Event log written by the recorder and the camera monitor, read by the notifier."""
from sqlalchemy.orm import Session

from app.models import Event

KINDS = ("motion_start", "motion_end", "camera_offline", "camera_online", "storage_error", "storage_ok",
         "disk_warning", "disk_ok", "video_lost", "video_ok", "camera_repair")


def emit(db: Session, kind: str, camera_id: int | None = None, detail: str | None = None) -> Event:
    """Adds an event to the session; the caller commits."""
    assert kind in KINDS, kind
    ev = Event(kind=kind, camera_id=camera_id, detail=detail)
    db.add(ev)
    return ev
