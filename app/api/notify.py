import asyncio
import json
from datetime import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import encrypt
from app.core.db import get_db
from app.core.security import audit, client_ip, require_role
from app.models import Camera, NotifyChannel, User
from app.notify import mqtt_bridge
from app.notify.channels import ChannelError, Message
from app.notify.notifier import CHANNEL_KINDS, channel_config, mqtt, send_push, settings_row

router = APIRouter(prefix="/api/notify", dependencies=[Depends(require_role("admin"))])

# allowed config fields per channel (secrets are separate and never returned)
CONFIG_FIELDS = {
    "telegram": {"chat_id"},
    "ntfy": {"server", "topic"},
    "email": {"host", "port", "security", "username", "sender", "recipients"},
    "mqtt": {"host", "port", "username", "prefix", "discovery", "discovery_prefix",
             "ha_image", "ha_controls", "ha_recording", "ha_ptz"},
}
SETTING_FIELDS = ("on_motion", "on_camera_offline", "on_camera_online", "on_storage", "with_snapshot",
                  "cooldown_enabled", "cooldown_minutes", "quiet_enabled", "quiet_start", "quiet_end")


def _channel_row(db: Session, kind: str) -> NotifyChannel:
    ch = db.get(NotifyChannel, kind)
    if ch is None:
        ch = NotifyChannel(kind=kind, config="{}")
        db.add(ch)
        db.flush()
    return ch


@router.get("")
def get_all(db: Session = Depends(get_db)):
    st = settings_row(db)
    chans = {}
    for kind in CHANNEL_KINDS:
        ch = _channel_row(db, kind)
        chans[kind] = {"enabled": ch.enabled, "config": json.loads(ch.config or "{}"), "has_secret": bool(ch.secret_enc),
                       "last_ok_at": ch.last_ok_at, "last_error": ch.last_error}
    db.commit()
    chans["mqtt"] |= {"connected": mqtt.connected, "error": mqtt.error}
    cams = db.execute(select(Camera).where(Camera.enabled.is_(True)).order_by(Camera.sort_order, Camera.name)).scalars()
    return {"settings": {f: getattr(st, f) for f in SETTING_FIELDS}, "channels": chans,
            "cameras": [{"slug": c.slug, "name": c.name, "notify_motion": c.notify_motion} for c in cams]}


class SettingsIn(BaseModel):
    on_motion: bool = True
    on_camera_offline: bool = True
    on_camera_online: bool = True
    on_storage: bool = True
    with_snapshot: bool = True
    cooldown_enabled: bool = True
    cooldown_minutes: int = Field(default=5, ge=1, le=1440)
    quiet_enabled: bool = False
    quiet_start: time | None = None
    quiet_end: time | None = None


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    if body.quiet_enabled and (body.quiet_start is None or body.quiet_end is None):
        raise HTTPException(400, "Za tihe sate unesi početak i kraj")
    st = settings_row(db)
    for f in SETTING_FIELDS:
        setattr(st, f, getattr(body, f))
    db.commit()
    audit(db, "notify_settings_set", user=user, detail=body.model_dump_json(), ip=client_ip(request))
    return {f: getattr(st, f) for f in SETTING_FIELDS}


class ChannelIn(BaseModel):
    enabled: bool = False
    config: dict = {}
    secret: str | None = Field(default=None, max_length=512)  # None: keep the stored one


def _clean_config(kind: str, config: dict) -> dict:
    out = {k: v for k, v in config.items() if k in CONFIG_FIELDS[kind] and v not in (None, "")}
    if "port" in out:
        try:
            out["port"] = int(out["port"])
        except (TypeError, ValueError):
            raise HTTPException(400, "Port mora biti broj")
    if kind == "email" and out.get("security") not in (None, "starttls", "ssl", "none"):
        raise HTTPException(400, "Bezbednost: starttls, ssl ili none")
    return out


def _check_kind(kind: str) -> None:
    if kind not in CHANNEL_KINDS:
        raise HTTPException(404, "Nepoznat kanal")


@router.put("/channels/{kind}")
def put_channel(kind: str, body: ChannelIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    _check_kind(kind)
    ch = _channel_row(db, kind)
    ch.enabled, ch.config = body.enabled, json.dumps(_clean_config(kind, body.config))
    if body.secret:
        ch.secret_enc = encrypt(body.secret)
    db.commit()
    audit(db, "notify_channel_set", user=user, target=kind,
          detail=f"enabled={body.enabled} secret={'changed' if body.secret else 'kept'}", ip=client_ip(request))
    return {"ok": True}


@router.post("/channels/{kind}/test")
async def test_channel(kind: str, body: ChannelIn, db: Session = Depends(get_db)):
    """Tests the settings in the form (not yet saved); without a new secret the stored one is used."""
    _check_kind(kind)
    cfg = _clean_config(kind, body.config)
    secret = body.secret or channel_config(db.get(NotifyChannel, kind))[2]
    try:
        if kind == "mqtt":
            await asyncio.to_thread(mqtt_bridge.test, cfg, secret)
        else:
            await send_push(kind, cfg, secret, Message("Probna poruka", "Obaveštenja sa kamera rade.", tags="bell"))
    except (ChannelError, mqtt_bridge.MqttError) as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


class CameraNotifyIn(BaseModel):
    notify_motion: bool


@router.put("/cameras/{slug}")
def put_camera(slug: str, body: CameraNotifyIn, db: Session = Depends(get_db)):
    cam = db.execute(select(Camera).where(Camera.slug == slug)).scalar_one_or_none()
    if cam is None:
        raise HTTPException(404, "Kamera ne postoji")
    cam.notify_motion = body.notify_motion
    db.commit()
    return {"slug": slug, "notify_motion": cam.notify_motion}
