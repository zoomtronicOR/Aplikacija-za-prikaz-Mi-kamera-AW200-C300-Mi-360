"""System page: server health, service status, cameras, disk alarm settings."""
import socket
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import audit, client_ip, require_role
from app.models import Camera, NotifyChannel, RecordingSettings, RecordingStorage, SystemAlert, User
from app.notify.notifier import mqtt, settings_row
from app.services import monitor, system

router = APIRouter(prefix="/api/system", dependencies=[Depends(require_role("admin"))])

FRESH = timedelta(seconds=90)


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


async def _go2rtc() -> dict:
    s = get_settings()
    try:
        async with httpx.AsyncClient(base_url=s.go2rtc_url, auth=(s.go2rtc_user, s.go2rtc_password), timeout=5) as c:
            info = (await c.get("/api")).json()
            streams = (await c.get("/api/streams")).json()
    except (httpx.HTTPError, ValueError) as e:
        return {"ok": False, "detail": f"ne odgovara ({e.__class__.__name__})"}
    active = sum(1 for st in streams.values() if st.get("producers") and any(p.get("medias") for p in st["producers"]))
    viewers = sum(len(st.get("consumers") or []) for st in streams.values())
    return {"ok": True, "detail": f"verzija {info.get('version', '?')}, aktivnih streamova {active}, gledalaca/potrošača {viewers}"}


@router.get("")
async def status(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    services = {}
    try:
        db.execute(text("select 1"))
        version = db.execute(text("select version_num from alembic_version")).scalar()
        services["database"] = {"ok": True, "detail": f"PostgreSQL, šema {version}"}
    except Exception as e:  # pragma: no cover - shown on the page
        services["database"] = {"ok": False, "detail": str(e)[:200]}
    services["go2rtc"] = await _go2rtc()

    last_rec = db.execute(select(func.max(RecordingSettings.status_at))).scalar()
    rec_alive = bool(last_rec and last_rec > now - FRESH)
    motion_cams = db.execute(select(func.count()).select_from(RecordingSettings).where(RecordingSettings.mode == "motion")).scalar()
    recording = db.execute(select(func.count()).select_from(RecordingSettings).where(
        RecordingSettings.rec_active.is_(True), RecordingSettings.status_at > now - FRESH)).scalar()
    services["recorder"] = {"ok": rec_alive, "detail": (f"snima {recording} kamera, detekcija pokreta na {motion_cams}"
                                                       if rec_alive else "ne javlja se duže od 90 s")}

    st = db.get(RecordingStorage, 1)
    if st and st.host:
        services["storage"] = {"ok": bool(st.status_ok), "detail": (st.status_error or
                               f"\\\\{st.host}\\{st.share}, čeka slanje {st.pending_bytes / 1024 ** 2:.0f} MB")}
    else:
        services["storage"] = {"ok": None, "detail": "nije podešeno"}

    services["https"] = {"ok": _port_open(443), "detail": "Caddy na portu 443"}
    ch = db.get(NotifyChannel, "mqtt")
    if ch and ch.enabled:
        services["mqtt"] = {"ok": mqtt.connected, "detail": "povezano sa brokerom" if mqtt.connected else (mqtt.error or "nije povezano")}

    cams = db.execute(select(Camera).where(Camera.enabled.is_(True)).order_by(Camera.sort_order, Camera.name)).scalars().all()
    settings = settings_row(db)
    alerts = {a.target: a.level for a in db.execute(select(SystemAlert)).scalars()}
    return {
        "metrics": system.snapshot(db),  # a few small /proc reads
        "services": services,
        "cameras": [{"name": c.name, "online": monitor.online.get(c.slug), "last_seen_at": c.last_seen_at} for c in cams],
        "alarm": {"on_disk": settings.on_disk, "disk_warn_pct": settings.disk_warn_pct,
                  "disk_crit_pct": settings.disk_crit_pct, "levels": alerts},
    }


class AlarmIn(BaseModel):
    on_disk: bool = True
    disk_warn_pct: int = Field(default=80, ge=50, le=98)
    disk_crit_pct: int = Field(default=90, ge=51, le=99)


@router.put("/alarm")
def put_alarm(body: AlarmIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    if body.disk_crit_pct <= body.disk_warn_pct:
        raise HTTPException(400, "Kritičan prag mora biti veći od praga upozorenja")
    st = settings_row(db)
    st.on_disk, st.disk_warn_pct, st.disk_crit_pct = body.on_disk, body.disk_warn_pct, body.disk_crit_pct
    db.commit()
    audit(db, "system_alarm_set", user=user, detail=body.model_dump_json(), ip=client_ip(request))
    return body
