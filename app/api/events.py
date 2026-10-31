"""Event archive: list with filters, thumbnails, and the recording that covers each motion event."""
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import audit, client_ip, current_user, require_role
from app.models import Camera, Event, Recording, User
from app.notify.notifier import settings_row

router = APIRouter(prefix="/api/events")

KIND_GROUPS = {
    "motion": ("motion_start",),
    "camera": ("camera_offline", "camera_online"),
    "storage": ("storage_error", "storage_ok"),
}
ARCHIVE_KINDS = tuple(k for ks in KIND_GROUPS.values() for k in ks)  # motion_end is folded into motion_start
RECORDING_SLACK_S = 15  # the pre-roll starts a recording up to ~10 s before the event


def _day_range(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), ZoneInfo(get_settings().timezone))
    return start, start + timedelta(days=1)


def _motion_end(db: Session, ev: Event) -> datetime | None:
    return db.execute(select(func.min(Event.ts)).where(
        Event.camera_id == ev.camera_id, Event.kind == "motion_end", Event.ts > ev.ts)).scalar()


def _recording_for(db: Session, ev: Event) -> tuple[int, int] | None:
    """(recording id, seconds into it) for the clip that contains the event's moment."""
    rec = db.execute(select(Recording).where(
        Recording.camera_id == ev.camera_id,
        Recording.started_at <= ev.ts + timedelta(seconds=RECORDING_SLACK_S),
        Recording.ended_at >= ev.ts).order_by(Recording.started_at).limit(1)).scalar_one_or_none()
    if rec is None:
        return None
    return rec.id, max(0, int((ev.ts - rec.started_at).total_seconds()) - 2)


@router.get("")
def list_events(camera: str | None = None, day: date | None = None,
                kind: Literal["all", "motion", "camera", "storage"] = "all",
                offset: int = 0, limit: int = 100, db: Session = Depends(get_db), user: User = Depends(current_user)):
    cams = {c.id: c for c in db.execute(select(Camera)).scalars()}
    q = select(Event).where(Event.kind.in_(ARCHIVE_KINDS if kind == "all" else KIND_GROUPS[kind]))
    if camera:
        cam = next((c for c in cams.values() if c.slug == camera), None)
        if cam is None:
            raise HTTPException(404, "Kamera ne postoji")
        q = q.where(Event.camera_id == cam.id)
    if day:
        start, end = _day_range(day)
        q = q.where(and_(Event.ts >= start, Event.ts < end))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(Event.ts.desc()).offset(max(0, offset)).limit(min(limit, 500))).scalars().all()
    has_thumb = set(db.execute(select(Event.id).where(Event.id.in_([e.id for e in rows]), Event.thumb.is_not(None))).scalars())

    items = []
    for ev in rows:
        cam = cams.get(ev.camera_id)
        item = {"id": ev.id, "ts": ev.ts, "kind": ev.kind, "detail": ev.detail,
                "camera": cam.slug if cam else None, "camera_name": cam.name if cam else None,
                "has_thumb": ev.id in has_thumb, "end": None, "recording": None}
        if ev.kind == "motion_start":
            item["end"] = _motion_end(db, ev)
            rec = _recording_for(db, ev)
            if rec:
                item["recording"] = {"id": rec[0], "offset_s": rec[1]}
        items.append(item)
    return {"total": total, "items": items}


@router.get("/timeline")
def timeline(day: date, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Per camera, the day's motion spans (start/end) and camera outages, for the 24 h strip."""
    start, end = _day_range(day)
    evs = db.execute(select(Event).where(Event.ts >= start - timedelta(hours=1), Event.ts < end,
                                         Event.kind.in_(("motion_start", "motion_end", "camera_offline", "camera_online")))
                     .order_by(Event.ts)).scalars().all()
    out: dict[int, dict] = {}
    open_motion: dict[int, Event] = {}
    open_down: dict[int, Event] = {}
    for ev in evs:
        lane = out.setdefault(ev.camera_id, {"motion": [], "down": []})
        if ev.kind == "motion_start":
            open_motion[ev.camera_id] = ev
        elif ev.kind == "motion_end" and ev.camera_id in open_motion:
            s = open_motion.pop(ev.camera_id)
            lane["motion"].append({"id": s.id, "start": max(s.ts, start), "end": ev.ts, "detail": s.detail})
        elif ev.kind == "camera_offline":
            open_down[ev.camera_id] = ev
        elif ev.kind == "camera_online" and ev.camera_id in open_down:
            lane["down"].append({"start": max(open_down.pop(ev.camera_id).ts, start), "end": ev.ts})
    for cid, s in open_motion.items():  # still moving (or end not recorded yet)
        out.setdefault(cid, {"motion": [], "down": []})["motion"].append(
            {"id": s.id, "start": max(s.ts, start), "end": None, "detail": s.detail})
    for cid, s in open_down.items():
        out.setdefault(cid, {"motion": [], "down": []})["down"].append({"start": max(s.ts, start), "end": None})
    cams = db.execute(select(Camera).where(Camera.enabled.is_(True)).order_by(Camera.sort_order, Camera.name)).scalars()
    return {"start": start, "end": end,
            "cameras": [{"slug": c.slug, "name": c.name, **out.get(c.id, {"motion": [], "down": []})} for c in cams]}


@router.get("/{event_id}/thumb")
def thumb(event_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    data = db.execute(select(Event.thumb).where(Event.id == event_id)).scalar()
    if not data:
        raise HTTPException(404, "Nema slike")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


class ArchiveSettingsIn(BaseModel):
    event_thumbs: bool = True
    events_keep_days: int = Field(default=30, ge=1, le=365)


@router.get("/settings")
def get_archive_settings(db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    st = settings_row(db)
    return {"event_thumbs": st.event_thumbs, "events_keep_days": st.events_keep_days}


@router.put("/settings")
def put_archive_settings(body: ArchiveSettingsIn, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require_role("admin"))):
    st = settings_row(db)
    st.event_thumbs, st.events_keep_days = body.event_thumbs, body.events_keep_days
    db.commit()
    audit(db, "events_settings_set", user=user, detail=body.model_dump_json(), ip=client_ip(request))
    return {"event_thumbs": st.event_thumbs, "events_keep_days": st.events_keep_days}
