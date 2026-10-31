"""Home Assistant side of the MQTT bridge: entity state, images, and commands coming back from HA.

Commands are audit-logged as user "mqtt". They act like the web UI: camera controls go through
the camera's driver, recording switches write `recording_settings` (the recorder picks them up),
and PTZ briefly opens the stream when nobody is watching, because the motor rides on the video
session.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import audit
from app.models import Camera, RecordingSettings
from app.notify.mqtt_bridge import REC_MODES, MqttBridge
from app.services import go2rtc, monitor
from app.services.controls import ControlError, capabilities, model_for

log = logging.getLogger(__name__)

CONTROL_POLL_S = 60
IMAGE_EVERY_S = 600
PTZ_OPS = {"left": 1, "right": 2, "up": 3, "down": 4}
_last_poll = 0.0
_last_image: dict[str, float] = {}


def entities(cams: list[Camera]) -> list[dict]:
    out = []
    for c in cams:
        caps = capabilities(c)
        out.append({"slug": c.slug, "name": c.name, "model": c.model, "controls": caps["controls"], "ptz": caps["ptz"]})
    return out


def _iso(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else "None"


def publish_recording(mqtt: MqttBridge, cams: list[Camera], rec: dict[int, RecordingSettings]) -> None:
    fresh_after = datetime.now(timezone.utc) - timedelta(seconds=90)
    for c in cams:
        r = rec.get(c.id)
        if r is None:
            continue
        alive = r.status_at is not None and r.status_at > fresh_after
        mqtt.set_state(c.slug, "state/rec_manual", "ON" if r.manual_active else "OFF")
        mqtt.set_state(c.slug, "state/rec_mode", REC_MODES.get(r.mode, r.mode))
        mqtt.set_state(c.slug, "state/recording", "ON" if alive and r.rec_active else "OFF")
        mqtt.set_state(c.slug, "state/last_motion", _iso(r.last_motion_at))


def _state_value(control: dict, value) -> str:
    if control["kind"] == "bool":
        return "ON" if value else "OFF"
    return control["options"].get(str(value), str(value))


async def poll_controls(mqtt: MqttBridge, cams: list[Camera]) -> None:
    """Reads control state from the cameras (every CONTROL_POLL_S) so HA shows changes made elsewhere."""
    global _last_poll
    if not mqtt.connected or not mqtt.group("ha_controls") or time.time() - _last_poll < CONTROL_POLL_S:
        return
    _last_poll = time.time()

    async def one(c: Camera):
        model = model_for(c)
        if model is None or monitor.online.get(c.slug) is False:
            return
        try:
            state = await asyncio.wait_for(model.get_state(c), 10)
        except (ControlError, asyncio.TimeoutError):
            return
        defs = {d["key"]: d for d in capabilities(c)["controls"]}
        for key, value in state.items():
            if key in defs:
                mqtt.set_state(c.slug, f"state/{key}", _state_value(defs[key], value))

    await asyncio.gather(*(one(c) for c in cams))


async def publish_image(mqtt: MqttBridge, cam: Camera, hd: bool) -> None:
    if mqtt.connected and mqtt.group("ha_image"):
        jpeg = await go2rtc.frame(cam.slug if hd or not cam.sd_stream else cam.sd_stream)
        mqtt.image(cam.slug, jpeg)
        _last_image[cam.slug] = time.time()


async def periodic_images(mqtt: MqttBridge, cams: list[Camera]) -> None:
    """One stale camera per pass, so cameras aren't all woken at once."""
    if not mqtt.connected or not mqtt.group("ha_image"):
        return
    due = [c for c in cams if monitor.online.get(c.slug) and time.time() - _last_image.get(c.slug, 0) > IMAGE_EVERY_S]
    if due:
        await publish_image(mqtt, due[0], hd=False)


# --- commands from HA ----------------------------------------------------------------------------

async def _ptz(cam: Camera, direction: str) -> None:
    """Motor steps need a live session: keep the stream open for a few seconds if nobody watches."""
    s = get_settings()

    async def hold_stream():
        try:
            async with httpx.AsyncClient(auth=(s.go2rtc_user, s.go2rtc_password), timeout=10) as c:
                async with c.stream("GET", f"{s.go2rtc_url}/api/stream.mp4", params={"src": cam.slug}) as r:
                    end = time.time() + 6
                    async for _ in r.aiter_bytes():
                        if time.time() > end:
                            break
        except httpx.HTTPError:
            pass

    holder = asyncio.create_task(hold_stream())
    try:
        for attempt in range(6):  # the session may need a moment to come up
            r = await go2rtc.motor(cam.slug, PTZ_OPS[direction])
            if r.status_code == 200:
                return
            await asyncio.sleep(1)
        raise ControlError("okretanje nije uspelo")
    finally:
        await asyncio.sleep(0.5)
        holder.cancel()


async def handle_command(mqtt: MqttBridge, slug: str, key: str, payload: str) -> None:
    global _last_poll
    with SessionLocal() as db:
        cam = db.execute(select(Camera).where(Camera.slug == slug, Camera.enabled.is_(True))).scalar_one_or_none()
        if cam is None:
            return
        try:
            if key == "ptz":
                if not mqtt.group("ha_ptz") or payload not in PTZ_OPS or not capabilities(cam)["ptz"]:
                    return
                await _ptz(cam, payload)
            elif key in ("rec_manual", "rec_mode"):
                if not mqtt.group("ha_recording"):
                    return
                rs = db.get(RecordingSettings, cam.id) or RecordingSettings(camera_id=cam.id)
                db.add(rs)
                if key == "rec_manual":
                    rs.manual_active = payload == "ON"
                    rs.manual_by = "mqtt" if rs.manual_active else None
                else:
                    mode = next((m for m, label in REC_MODES.items() if label == payload), None)
                    if mode is None or (mode == "schedule" and not (rs.schedule_start and rs.schedule_end)):
                        raise ControlError("nepoznat način ili raspored nije podešen u aplikaciji")
                    rs.mode = mode
                db.commit()
                publish_recording(mqtt, [cam], {cam.id: rs})
            else:
                if not mqtt.group("ha_controls"):
                    return
                model = model_for(cam)
                control = next((c for c in capabilities(cam)["controls"] if c["key"] == key), None)
                if model is None or control is None:
                    return
                if control["kind"] == "bool":
                    value = payload == "ON"
                else:
                    value = next((v for v, label in control["options"].items() if label == payload), None)
                    if value is None:
                        raise ControlError(f"nepoznata vrednost {payload}")
                await model.set(cam, key, value)
                mqtt.set_state(slug, f"state/{key}", _state_value(control, value))
            audit(db, "mqtt_command", username="mqtt", target=slug, detail=f"{key}={payload}")
        except ControlError as e:
            log.warning("mqtt command %s/%s=%s failed: %s", slug, key, payload, e)
            audit(db, "mqtt_command_failed", username="mqtt", target=slug, detail=f"{key}={payload}: {e}")
            # put HA's entity back to the real state on the next poll / recording publish
            mqtt.forget_state(slug, f"state/{key}")
            _last_poll = 0
