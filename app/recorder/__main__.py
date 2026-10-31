"""Recorder service: `python -m app.recorder` (its own container, independent of the web app).

The web app only writes settings and the manual REC flag to the DB; this process polls them,
runs one ffmpeg segmenter per recording camera, uploads finished clips to the SMB share,
and reports status back to the DB.
"""
import logging
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Camera, RecordingSettings, RecordingStorage
from app.recorder.schedule import in_window
from app.recorder.uploader import apply_retention, buffer_bytes, enforce_buffer_cap, upload_pending
from app.recorder.worker import CameraWorker, recover_leftovers
from app.services import smb

log = logging.getLogger("recorder")

LOOP_S = 2
UPLOAD_EVERY_S = 5
RETENTION_EVERY_S = 600
STATUS_EVERY_S = 30

stopping = threading.Event()


def wanted_reason(rs: RecordingSettings, now_local: datetime) -> str | None:
    if rs.manual_active:
        return "manual"
    if rs.mode == "continuous":
        return "continuous"
    if rs.mode == "schedule" and in_window(now_local, rs.schedule_start, rs.schedule_end, rs.schedule_days):
        return "schedule"
    return None  # "motion" is handled in a later stage


def record_loop(buffer_dir: Path, tz: ZoneInfo) -> None:
    s = get_settings()
    workers: dict[str, CameraWorker] = {}
    last_status: dict[str, tuple] = {}
    last_status_at = 0.0
    while not stopping.is_set():
        now = time.time()
        try:
            with SessionLocal() as db:
                cams = db.execute(select(Camera).where(Camera.enabled.is_(True))).scalars().all()
                settings = {r.camera_id: r for r in db.execute(select(RecordingSettings)).scalars()}
                for cam in cams:
                    if cam.id not in settings:
                        settings[cam.id] = RecordingSettings(camera_id=cam.id)
                        db.add(settings[cam.id])
                db.commit()

                now_local = datetime.fromtimestamp(now, tz)
                active = set()
                for cam in cams:
                    rs = settings[cam.id]
                    w = workers.get(cam.slug)
                    if w is None:
                        w = workers[cam.slug] = CameraWorker(cam.slug, f"{s.go2rtc_rtsp_url}/{cam.slug}", buffer_dir)
                    reason = wanted_reason(rs, now_local)
                    w.tick(reason, now)
                    active.add(cam.slug)

                    status = (w.running, reason if w.running else None, w.last_error)
                    if status != last_status.get(cam.slug) or now - last_status_at > STATUS_EVERY_S:
                        rs.rec_active, rs.rec_reason, rs.last_error = status
                        rs.status_at = datetime.now(timezone.utc)
                        last_status[cam.slug] = status
                if now - last_status_at > STATUS_EVERY_S:
                    last_status_at = now
                db.commit()

            for slug in set(workers) - active:  # disabled or deleted cameras
                workers.pop(slug).shutdown()
        except Exception:
            log.exception("record loop failed")
        stopping.wait(LOOP_S)

    for w in workers.values():
        w.shutdown()


def upload_loop(buffer_dir: Path, tz: ZoneInfo) -> None:
    clip_dir = buffer_dir / "clips"
    last_share = None
    last_retention = 0.0
    while not stopping.is_set():
        try:
            with SessionLocal() as db:
                st = db.get(RecordingStorage, 1)
                if st is None:
                    st = RecordingStorage(id=1)
                    db.add(st)
                    db.commit()
                ok, error = None, None
                try:
                    share = smb.load(db)
                except ValueError as e:
                    share, error, ok = None, str(e), False
                if share != last_share:
                    smb.reset()  # credentials or target changed
                    last_share = share
                if share is None:
                    if error is None and any(clip_dir.glob("*")):
                        ok, error = False, "skladište za snimke nije podešeno"
                else:
                    try:
                        upload_pending(db, share, clip_dir, tz)
                        if time.time() - last_retention > RETENTION_EVERY_S:
                            apply_retention(db, share)
                            last_retention = time.time()
                        ok = True
                    except smb.StorageError as e:
                        ok, error = False, str(e)
                        log.warning("storage: %s", e)
                dropped = enforce_buffer_cap(clip_dir, int(st.buffer_max_gb * 1024 ** 3))
                if dropped:
                    error = (error + "; " if error else "") + f"lokalni bafer pun, obrisano {len(dropped)} snimaka"
                st.status_ok, st.status_error = ok, error
                st.status_at = datetime.now(timezone.utc)
                st.pending_bytes = buffer_bytes(buffer_dir)
                db.commit()
        except Exception:
            log.exception("upload loop failed")
        stopping.wait(UPLOAD_EVERY_S)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("smbprotocol").setLevel(logging.WARNING)  # logs every file open/close at INFO
    s = get_settings()
    buffer_dir = Path(s.recorder_buffer_dir)
    buffer_dir.mkdir(parents=True, exist_ok=True)
    tz = ZoneInfo(s.timezone)
    recover_leftovers(buffer_dir)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.set())

    uploader = threading.Thread(target=upload_loop, args=(buffer_dir, tz), name="uploader", daemon=True)
    uploader.start()
    log.info("recorder started, buffer %s", buffer_dir)
    record_loop(buffer_dir, tz)  # returns after SIGTERM, with clips of the last segments flushed
    log.info("recorder stopped")


if __name__ == "__main__":
    main()
