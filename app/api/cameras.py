import re
import unicodedata
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.recordings import rec_json
from app.api.stream import go2rtc_stream
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import audit, client_ip, current_user, require_role
from app.models import Camera, RecordingSettings, User
from app.services import go2rtc, monitor
from app.services.controls import ControlError, capabilities, detect_esp32, model_for

router = APIRouter(prefix="/api/cameras")

CONTROL_ROLES = ("admin", "user")
# go2rtc /api/xiaomi/motor operations (custom build with go2rtc PR #2531)
PTZ_OPERATIONS = {"left": 1, "right": 2, "up": 3, "down": 4}


class SetControl(BaseModel):
    key: str
    value: bool | str


class NewCamera(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    source: str = Field(min_length=1, max_length=1024)
    sd_source: str | None = Field(default=None, max_length=1024)
    mode: Literal["direct", "h264", "mjpeg"] = "direct"
    skip_probe: bool = False


class UpdateCamera(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    sort_order: int | None = None
    ptz: bool | None = None  # pan/tilt arrows on/off for this camera


def get_camera(db: Session, slug: str, include_disabled: bool = False) -> Camera:
    q = select(Camera).where(Camera.slug == slug)
    if not include_disabled:
        q = q.where(Camera.enabled.is_(True))
    cam = db.execute(q).scalar_one_or_none()
    if cam is None:
        raise HTTPException(404, "Kamera ne postoji")
    return cam


def slugify(name: str, taken: set[str]) -> str:
    base = unicodedata.normalize("NFKD", name.replace("đ", "dj").replace("Đ", "Dj"))
    base = re.sub(r"[^a-z0-9]+", "_", base.encode("ascii", "ignore").decode().lower()).strip("_")[:48] or "kamera"
    slug, i = base, 2
    while slug in taken or f"{slug}_sd" in taken or slug.endswith("_sd"):
        slug, i = f"{base}_{i}", i + 1
    return slug


def camera_json(c: Camera, user: User) -> dict:
    item = {
        "slug": c.slug, "name": c.name, "location": c.location, "icon": c.icon, "kind": c.kind, "model": c.model,
        "enabled": c.enabled, "sort_order": c.sort_order,
        "online": monitor.online.get(c.slug), "last_seen_at": c.last_seen_at,
        "can_control": user.role in CONTROL_ROLES, "has_sd": bool(c.sd_stream), **capabilities(c),
    }
    if user.role == "admin":  # sensitive identifiers are admin-only (spec 5.5)
        item |= {"did": c.did, "ip": c.ip, "mac": c.mac, "region": c.region, "source": c.source_display}
    return item


@router.get("")
def list_cameras(all: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = select(Camera).order_by(Camera.sort_order, Camera.name)
    if not (all and user.role == "admin"):
        q = q.where(Camera.enabled.is_(True))
    return [{**camera_json(c, user), "rec": rec_json(db.get(RecordingSettings, c.id))} for c in db.execute(q).scalars()]


@router.post("", status_code=201)
async def add_camera(body: NewCamera, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin"))):
    try:
        source = go2rtc.validate_source(body.source)
        sd_source = go2rtc.validate_source(body.sd_source) if body.sd_source else None
    except go2rtc.SourceError as e:
        raise HTTPException(400, str(e))

    resolution = None
    if not body.skip_probe:
        try:
            resolution = await go2rtc.probe(go2rtc.probe_source(source, body.mode))
        except go2rtc.SourceError as e:
            raise HTTPException(422, str(e))

    taken = set(db.execute(select(Camera.slug)).scalars()) | set(db.execute(select(Camera.sd_stream)).scalars())
    slug = slugify(body.name, taken)
    await go2rtc.put_stream(slug, go2rtc.build_sources(source, body.mode))
    if sd_source:
        await go2rtc.put_stream(f"{slug}_sd", go2rtc.build_sources(sd_source, body.mode))

    hp = go2rtc.host_port(source)
    model = await detect_esp32(hp[0]) if hp and body.mode == "mjpeg" else None
    cam = Camera(slug=slug, name=body.name.strip(), location=(body.location or "").strip() or None, kind="generic",
                 source_display=go2rtc.mask_source(source), ip=hp[0] if hp else None, model=model,
                 sd_stream=f"{slug}_sd" if sd_source else None,
                 sort_order=(db.execute(select(func.max(Camera.sort_order))).scalar() or 0) + 1)
    db.add(cam)
    db.commit()
    audit(db, "camera_added", user=user, target=slug, detail=cam.source_display, ip=client_ip(request))
    return {**camera_json(cam, user), "resolution": resolution}


@router.post("/probe")
async def probe_source(body: NewCamera, user: User = Depends(require_role("admin"))):
    """Lets the admin test a URL before saving."""
    try:
        source = go2rtc.validate_source(body.source)
        return {"resolution": await go2rtc.probe(go2rtc.probe_source(source, body.mode))}
    except go2rtc.SourceError as e:
        raise HTTPException(422, str(e))


@router.patch("/{slug}")
def update_camera(slug: str, body: UpdateCamera, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_role("admin"))):
    cam = get_camera(db, slug, include_disabled=True)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(cam, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    audit(db, "camera_updated", user=user, target=slug, detail=str(changes), ip=client_ip(request))
    return camera_json(cam, user)


@router.delete("/{slug}", status_code=204)
async def delete_camera(slug: str, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require_role("admin"))):
    cam = get_camera(db, slug, include_disabled=True)
    # removing the go2rtc streams also keeps import-cameras from bringing a Xiaomi camera back;
    # its token stays in the tokens file, so adding it again later is instant
    await go2rtc.delete_stream(cam.slug)
    if cam.sd_stream:
        await go2rtc.delete_stream(cam.sd_stream)
    db.delete(cam)
    db.commit()
    audit(db, "camera_deleted", user=user, target=slug, ip=client_ip(request))


@router.get("/{slug}/snapshot")
async def snapshot(slug: str, quality: Literal["hd", "sd"] = "hd", download: bool = False,
                   db: Session = Depends(get_db), user: User = Depends(require_role(*CONTROL_ROLES))):
    """Current picture. quality=sd is what the motion detector analyses (the zone editor uses it)."""
    cam = get_camera(db, slug)
    data = await go2rtc.frame(go2rtc_stream(cam, quality))
    if not data:
        raise HTTPException(502, "Kamera nije poslala sliku")
    headers = {"Cache-Control": "no-store"}
    if download:
        stamp = datetime.now(ZoneInfo(get_settings().timezone)).strftime("%Y-%m-%d_%H-%M-%S")
        headers["Content-Disposition"] = f'attachment; filename="{cam.slug}_{stamp}.jpg"'
    return Response(data, media_type="image/jpeg", headers=headers)


@router.get("/{slug}/state")
async def get_state(slug: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    cam = get_camera(db, slug)
    model = model_for(cam)
    if model is None:
        return {}
    try:
        return await model.get_state(cam)
    except ControlError as e:
        raise HTTPException(502, str(e))


@router.post("/{slug}/state")
async def set_state(slug: str, body: SetControl, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_role(*CONTROL_ROLES))):
    cam = get_camera(db, slug)
    model = model_for(cam)
    control = model.controls.get(body.key) if model else None
    if control is None:
        raise HTTPException(400, "Kamera ne podržava ovu kontrolu")
    try:
        value = control.coerce(body.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        await model.set(cam, body.key, value)
    except ControlError as e:
        audit(db, "camera_set_failed", user=user, target=cam.slug, detail=f"{body.key}={value}: {e}", ip=client_ip(request))
        raise HTTPException(502, str(e))
    audit(db, "camera_set", user=user, target=cam.slug, detail=f"{body.key}={value}", ip=client_ip(request))
    return {"key": body.key, "value": value}


@router.post("/{slug}/ptz/{direction}")
async def ptz_step(slug: str, direction: str, quality: str = "hd", db: Session = Depends(get_db),
                   user: User = Depends(require_role(*CONTROL_ROLES))):
    """One pan/tilt step. Not audit-logged: a held arrow sends a step every ~400 ms.

    The motor command rides on a live session, so it goes to the stream the viewer is watching.
    """
    cam = get_camera(db, slug)
    model = model_for(cam)
    if model is None or not model.ptz or direction not in PTZ_OPERATIONS:
        raise HTTPException(400, "Kamera ne podržava okretanje u tom smeru")
    r = await go2rtc.motor(go2rtc_stream(cam, quality), PTZ_OPERATIONS[direction])
    if r.status_code == 404 and "no stream" not in r.text and "motor" not in r.text:
        raise HTTPException(501, "go2rtc ne podržava okretanje (potrebna je verzija sa PTZ izmenom)")
    if r.status_code != 200:
        raise HTTPException(502, "Okretanje nije uspelo: kamera mora biti uživo na ekranu")
    return {"ok": True}


@router.post("/{slug}/actions/{name}")
async def run_action(slug: str, name: str, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin"))):
    cam = get_camera(db, slug)
    model = model_for(cam)
    if model is None or name not in model.actions:
        raise HTTPException(400, "Kamera ne podržava ovu radnju")
    try:
        await model.action(cam, name)
    except ControlError as e:
        raise HTTPException(502, str(e))
    audit(db, "camera_action", user=user, target=cam.slug, detail=name, ip=client_ip(request))
    return {"ok": True}
