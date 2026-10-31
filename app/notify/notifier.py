"""Turns events into notifications. Runs as a background task in the web app.

Every pass:
1. Syncs MQTT state: camera list, online/offline.
2. Takes unprocessed events. MQTT always gets them, as Home Assistant state.
3. Push channels (Telegram, ntfy, email) get a message only when its rules pass. Each rule has
   its own switch: the event type, the camera's motion switch, the motion cooldown, quiet hours
   (motion only). Events older than MAX_AGE_S are not pushed (e.g. after downtime).
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.core.db import SessionLocal
from app.models import Camera, Event, NotifyChannel, NotifySettings, RecordingSettings
from app.notify import ha
from app.notify.channels import SENDERS, ChannelError, Message
from app.notify.mqtt_bridge import MqttBridge
from app.recorder.schedule import in_window
from app.services import go2rtc, monitor

log = logging.getLogger(__name__)

LOOP_S = 2
MAX_AGE_S = 300
EVENT_KEEP_DAYS = 30
CHANNEL_KINDS = ("telegram", "mqtt", "ntfy", "email")

mqtt = MqttBridge()
_last_motion_push: dict[int, float] = {}
_ha_task: asyncio.Task | None = None


async def _ha_background(cams: list[Camera]) -> None:
    try:
        await ha.poll_controls(mqtt, cams)
        await ha.periodic_images(mqtt, cams)
    except Exception:
        log.exception("home assistant state update failed")


def channel_config(ch: NotifyChannel | None) -> tuple[bool, dict, str | None]:
    if ch is None:
        return False, {}, None
    secret = None
    if ch.secret_enc:
        try:
            secret = decrypt(ch.secret_enc)
        except ValueError:
            secret = None
    return ch.enabled, json.loads(ch.config or "{}"), secret


def settings_row(db) -> NotifySettings:
    st = db.get(NotifySettings, 1)
    if st is None:
        st = NotifySettings(id=1)
        db.add(st)
        db.commit()
    return st


def _message(ev: Event, cam: Camera | None, tz: ZoneInfo) -> Message:
    name = cam.name if cam else "kamera"
    where = f" ({cam.location})" if cam and cam.location else ""
    at = ev.ts.astimezone(tz).strftime("%H:%M:%S")
    if ev.kind == "motion_start":
        if ev.detail:  # AI label, e.g. "osoba 87%"
            what, _, score = ev.detail.rpartition(" ")
            return Message(f"{what.capitalize()}: {name}", f"{what.capitalize()} ({score}) — {name}{where} u {at}",
                           tags="walking", priority="high")
        return Message(f"Pokret: {name}", f"{name}{where} u {at}", tags="walking", priority="high")
    if ev.kind == "camera_offline":
        return Message(f"Kamera nedostupna: {name}", f"{name}{where} ne odgovara od {at}.", tags="warning")
    if ev.kind == "camera_online":
        return Message(f"Kamera ponovo radi: {name}", f"{name}{where} je ponovo dostupna u {at}.", tags="white_check_mark")
    if ev.kind == "storage_error":
        return Message("Problem sa snimanjem", f"Snimci ne mogu da se sačuvaju ({at}): {ev.detail or 'nepoznata greška'}",
                       tags="warning", priority="high")
    return Message("Snimanje ponovo radi", f"Skladište za snimke je ponovo dostupno ({at}).", tags="white_check_mark")


def _should_push(ev: Event, cam: Camera | None, st: NotifySettings, now: float, tz: ZoneInfo) -> bool:
    if now - ev.ts.timestamp() > MAX_AGE_S:
        return False
    if ev.kind == "motion_start":
        if not (st.on_motion and cam and cam.notify_motion):
            return False
        if st.quiet_enabled and in_window(datetime.fromtimestamp(now, tz), st.quiet_start, st.quiet_end, "0123456"):
            return False
        if st.cooldown_enabled and now - _last_motion_push.get(cam.id, 0) < st.cooldown_minutes * 60:
            return False
        return True
    return {"camera_offline": st.on_camera_offline, "camera_online": st.on_camera_online,
            "storage_error": st.on_storage, "storage_ok": st.on_storage}.get(ev.kind, False)


async def send_push(kind: str, cfg: dict, secret: str | None, msg: Message) -> None:
    await asyncio.to_thread(SENDERS[kind], cfg, secret, msg)


async def process_once(tz: ZoneInfo) -> None:
    with SessionLocal() as db:
        st = settings_row(db)
        channels = {c.kind: c for c in db.execute(select(NotifyChannel)).scalars()}
        cams = {c.id: c for c in db.execute(select(Camera)).scalars()}

        enabled, cfg, secret = channel_config(channels.get("mqtt"))
        mqtt.configure(enabled, cfg, secret)
        active_cams = [c for c in cams.values() if c.enabled]
        mqtt.set_cameras(ha.entities(active_cams))
        rec = {r.camera_id: r for r in db.execute(select(RecordingSettings)).scalars()}
        ha.publish_recording(mqtt, active_cams, rec)
        for c in cams.values():
            if not c.enabled:
                continue
            if c.slug in monitor.online:
                mqtt.set_state(c.slug, "status", "online" if monitor.online[c.slug] else "offline")
            # motion state from the recorder's live status, not from the last event: self-healing
            # after restarts or lost events
            r = rec.get(c.id)
            fresh = r is not None and r.status_at is not None and \
                r.status_at > datetime.now(timezone.utc) - timedelta(seconds=90)  # recorder alive
            moving = bool(fresh and r.mode == "motion" and r.rec_active and r.rec_reason == "motion")
            mqtt.set_state(c.slug, "motion", "ON" if moving else "OFF")

        evs = db.execute(select(Event).where(Event.processed.is_(False)).order_by(Event.id).limit(50)).scalars().all()
        for ev in evs:
            ev.processed = True
            cam = cams.get(ev.camera_id)
            if cam and ev.kind in ("motion_start", "motion_end"):
                mqtt.event(cam.slug, "motion", ev.kind == "motion_start")
                if ev.kind == "motion_start" and time.time() - ev.ts.timestamp() < MAX_AGE_S:
                    asyncio.create_task(ha.publish_image(mqtt, cam, hd=True))
            elif cam:
                mqtt.event(cam.slug, ev.kind, None)
        db.commit()

        # camera I/O in the background, so a slow camera never delays a notification
        global _ha_task
        if _ha_task is None or _ha_task.done():
            _ha_task = asyncio.create_task(_ha_background(active_cams))

        now = time.time()
        for ev in evs:
            cam = cams.get(ev.camera_id)
            if not _should_push(ev, cam, st, now, tz):
                continue
            targets = [(k, *channel_config(channels.get(k))) for k in ("telegram", "ntfy", "email")]
            targets = [(k, c, s) for k, en, c, s in targets if en]
            if not targets:
                continue
            msg = _message(ev, cam, tz)
            if ev.kind == "motion_start":
                _last_motion_push[cam.id] = now
                if st.with_snapshot:
                    msg.image = await go2rtc.frame(cam.slug) or None
            results = await asyncio.gather(*(send_push(k, c, s, msg) for k, c, s in targets), return_exceptions=True)
            for (k, _, _), res in zip(targets, results):
                ch = channels[k]
                if isinstance(res, Exception):
                    ch.last_error = str(res) if isinstance(res, ChannelError) else f"{res.__class__.__name__}: {res}"
                    log.warning("notify %s failed: %s", k, ch.last_error)
                else:
                    ch.last_ok_at, ch.last_error = datetime.now(timezone.utc), None
            db.commit()


def prune_events(days: int = EVENT_KEEP_DAYS) -> None:
    with SessionLocal() as db:
        db.execute(delete(Event).where(Event.ts < datetime.now(timezone.utc) - timedelta(days=days)))
        db.commit()


async def notifier_loop() -> None:
    tz = ZoneInfo(get_settings().timezone)
    mqtt.loop = asyncio.get_running_loop()
    mqtt.on_command = lambda slug, key, payload: ha.handle_command(mqtt, slug, key, payload)
    pruned = 0.0
    while True:
        try:
            await process_once(tz)
            if time.time() - pruned > 3600:
                await asyncio.to_thread(prune_events)
                pruned = time.time()
        except Exception:
            log.exception("notifier failed")
        await asyncio.sleep(LOOP_S)
