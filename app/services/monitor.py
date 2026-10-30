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
from app.services import go2rtc, miio

log = logging.getLogger(__name__)

# slug -> online; read by the cameras API
online: dict[str, bool] = {}


def tcp_ping(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def reachable(cam: Camera) -> bool:
    if cam.kind == "xiaomi":
        return miio.ping(cam.ip)
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
        db.commit()


async def monitor_loop() -> None:
    while True:
        try:
            await check_once()
        except Exception:
            log.exception("camera monitor failed")
        await asyncio.sleep(get_settings().monitor_interval_s)
