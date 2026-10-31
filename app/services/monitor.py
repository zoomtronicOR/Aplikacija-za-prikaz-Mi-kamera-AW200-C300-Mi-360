"""Background reachability check: miIO hello for Xiaomi cameras, a TCP connect to the stream port
for generic ones. Records online state."""
import asyncio
import logging
import socket
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Camera
from app.services import events, go2rtc, miio

log = logging.getLogger(__name__)

# slug -> online; read by the cameras API
online: dict[str, bool] = {}
# camera_offline only after this many failed checks in a row (a single lost UDP packet isn't an outage)
OFFLINE_AFTER = 2
_fails: dict[str, int] = {}
_reported_down: set[str] = set()


def tcp_ping(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def reachable(cam: Camera) -> bool:
    if cam.kind == "xiaomi":
        return miio.ping(cam.ip)
    if (cam.model or "").startswith("esp32-") and cam.ip:
        # the stream port (81) serves one client, which is go2rtc while it streams; the control
        # server on 80 answers alongside it
        return tcp_ping(cam.ip, 80)
    hp = go2rtc.host_port(cam.source_display or "")
    return tcp_ping(*hp) if hp else False


async def check_once() -> None:
    with SessionLocal() as db:
        cams = db.execute(select(Camera).where(Camera.enabled.is_(True))).scalars().all()
        results = await asyncio.gather(*(asyncio.to_thread(reachable, c) for c in cams))
        now = datetime.now(timezone.utc)
        for cam, ok in zip(cams, results):
            if online.get(cam.slug) != ok:
                log.info("camera %s is %s", cam.slug, "online" if ok else "offline")
            online[cam.slug] = ok
            if ok:
                cam.last_seen_at = now
                _fails[cam.slug] = 0
                if cam.slug in _reported_down:
                    _reported_down.discard(cam.slug)
                    events.emit(db, "camera_online", cam.id)
            else:
                _fails[cam.slug] = _fails.get(cam.slug, 0) + 1
                if _fails[cam.slug] == OFFLINE_AFTER:
                    _reported_down.add(cam.slug)
                    events.emit(db, "camera_offline", cam.id)
        db.commit()


async def monitor_loop() -> None:
    while True:
        try:
            await check_once()
        except Exception:
            log.exception("camera monitor failed")
        await asyncio.sleep(get_settings().monitor_interval_s)
