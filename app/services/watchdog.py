"""Watchdog: notices cameras that answer on the network but stop delivering video, and repairs them.

Every pass (CHECK_S), for each enabled camera that the monitor sees online:
1. If one of its go2rtc streams is live and its byte counter grew, the camera is fine. Nothing to wake.
2. Otherwise, at most every HEALTHY_RECHECK_S, grab one frame from the SD stream.
3. After FAIL_LIMIT failed probes in a row: event video_lost. If repair is switched on, the camera
   gets a reboot over miIO (models with a restart action), or else a sleep/wake through its power
   control, which restarts its video pipeline. At most once per REPAIR_COOLDOWN_S per camera.
4. When video comes back: event video_ok.
Offline cameras are left to the monitor (camera_offline); miIO can't reach them anyway.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Camera, NotifySettings
from app.services import events, go2rtc, monitor
from app.services.controls import ControlError, model_for

log = logging.getLogger(__name__)

CHECK_S = 60
HEALTHY_RECHECK_S = 300
FAILING_RECHECK_S = 60
FAIL_LIMIT = 2
REPAIR_COOLDOWN_S = 3600
SLEEP_WAKE_PAUSE_S = 5


@dataclass
class CamState:
    bytes_seen: dict[str, int] = field(default_factory=dict)
    next_probe: float = 0.0
    fails: int = 0
    lost: bool = False
    repaired_at: float = 0.0
    last_ok: float | None = None
    last_action: str | None = None


state: dict[str, CamState] = {}  # slug -> state; read by the system page


async def _stream_bytes() -> dict[str, int]:
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{s.go2rtc_url}/api/streams", auth=(s.go2rtc_user, s.go2rtc_password))
            streams = r.json()
    except (httpx.HTTPError, ValueError):
        return {}
    return {name: sum(p.get("bytes_recv") or 0 for p in st.get("producers") or [])
            for name, st in streams.items()}


async def _repair(cam: Camera) -> str | None:
    """Reboots the camera if its driver can, else sleeps and wakes it. Returns what was done."""
    model = model_for(cam)
    if model is None:
        return None
    try:
        if "restart" in getattr(model, "actions", {}):
            await model.action(cam, "restart")
            return "restart kamere"
        if "power" in getattr(model, "props", {}):
            await model.set(cam, "power", False)
            await asyncio.sleep(SLEEP_WAKE_PAUSE_S)
            await model.set(cam, "power", True)
            return "uspavljivanje i buđenje kamere"
    except ControlError as e:
        log.warning("%s: repair failed: %s", cam.slug, e)
        return f"popravka nije uspela ({e})"
    return None


async def check_once() -> None:
    with SessionLocal() as db:
        ns = db.get(NotifySettings, 1)
        if ns is not None and not ns.watchdog_enabled:
            return
        repair_on = ns.watchdog_repair if ns else True
        cams = db.execute(select(Camera).where(Camera.enabled.is_(True))).scalars().all()
        counters = await _stream_bytes()
        now = time.time()
        for cam in cams:
            st = state.setdefault(cam.slug, CamState())
            if monitor.online.get(cam.slug) is not True:
                continue  # offline (or not checked yet): the monitor reports that
            names = [n for n in (cam.slug, cam.sd_stream) if n]
            grew = any(counters.get(n, 0) > st.bytes_seen.get(n, 0) for n in names if n in counters)
            st.bytes_seen = {n: counters.get(n, 0) for n in names}
            if grew:
                ok = True
            elif now >= st.next_probe:
                ok = bool(await go2rtc.frame(cam.sd_stream or cam.slug))
            else:
                continue

            if ok:
                st.fails, st.last_ok, st.next_probe = 0, now, now + HEALTHY_RECHECK_S
                if st.lost:
                    st.lost = False
                    events.emit(db, "video_ok", cam.id)
                continue

            st.fails += 1
            st.next_probe = now + FAILING_RECHECK_S
            log.info("%s: no video (%d)", cam.slug, st.fails)
            if st.fails == FAIL_LIMIT:
                st.lost = True
                events.emit(db, "video_lost", cam.id)
            if st.fails >= FAIL_LIMIT and repair_on and now - st.repaired_at > REPAIR_COOLDOWN_S:
                st.repaired_at = now
                action = await _repair(cam)
                if action:
                    st.last_action = action
                    events.emit(db, "camera_repair", cam.id, action)
        db.commit()


async def watchdog_loop() -> None:
    await asyncio.sleep(CHECK_S)  # let the monitor see the cameras first
    while True:
        try:
            await check_once()
        except Exception:
            log.exception("watchdog failed")
        await asyncio.sleep(CHECK_S)
