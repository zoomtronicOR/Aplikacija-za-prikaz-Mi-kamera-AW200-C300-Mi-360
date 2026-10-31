"""Server health and the disk alarm.

CPU, RAM, load and uptime come from scripts/hoststats.py, which runs in the LXC itself (systemd) and
writes data/hoststats/stats.json, mounted at /host/stats. lxcfs answers /proc reads relative to the
reader's cgroup, so from inside Docker they would only describe the app container. The root disk
is read with statvfs on a bind mount (/host/fs): filesystem stats don't have that problem.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import NotifySettings, RecordingStorage, SystemAlert
from app.services import events

STATS_FILE = "/host/stats/stats.json"
STATS_MAX_AGE_S = 60
FS = "/host/fs" if os.path.isdir("/host/fs") else "/"
HYSTERESIS_PCT = 5  # back to "ok" only this far below the warning threshold
SHARE_FRESH = timedelta(minutes=15)


def host_stats() -> dict | None:
    try:
        with open(STATS_FILE) as f:
            s = json.load(f)
    except (OSError, ValueError):
        return None
    return s if time.time() - s.get("ts", 0) < STATS_MAX_AGE_S else None


def server_disk() -> dict:
    s = os.statvfs(FS)
    total, free = s.f_blocks * s.f_frsize, s.f_bavail * s.f_frsize
    return {"total": total, "free": free, "pct": round(100 * (total - free) / total, 1)}


def share_disk(st: RecordingStorage | None) -> dict | None:
    if not st or not st.share_total_bytes or not st.status_at or \
            st.status_at < datetime.now(timezone.utc) - SHARE_FRESH:
        return None
    total, free = st.share_total_bytes, st.share_free_bytes or 0
    return {"total": total, "free": free, "pct": round(100 * (total - free) / total, 1)}


def snapshot(db: Session) -> dict:
    h = host_stats()
    memory = None
    if h:
        used = h["mem_total"] - h["mem_available"]
        memory = {"total": h["mem_total"], "used": used, "pct": round(100 * used / h["mem_total"], 1)}
    return {"stats_ok": h is not None, "cpu_pct": h and h["cpu_pct"], "cpus": h and h["cpus"],
            "load": h and h["load"], "memory": memory, "uptime_s": h and h["uptime_s"],
            "server_disk": server_disk(), "share_disk": share_disk(db.get(RecordingStorage, 1))}


def _gb(b: int) -> str:
    return f"{b / 1024 ** 3:.1f} GB"


def check_disks(db: Session) -> None:
    """Fires disk_warning when a disk crosses the warning/critical threshold, disk_ok when it recovers."""
    ns = db.get(NotifySettings, 1)
    warn, crit = (ns.disk_warn_pct, ns.disk_crit_pct) if ns else (80, 90)
    disks = {"server_disk": ("Disk servera", server_disk()),
             "share": ("Disk sa snimcima", share_disk(db.get(RecordingStorage, 1)))}
    for target, (label, d) in disks.items():
        if d is None:
            continue
        alert = db.get(SystemAlert, target) or SystemAlert(target=target, level=0)
        db.add(alert)
        level = 2 if d["pct"] >= crit else 1 if d["pct"] >= warn else 0
        if level == 0 and alert.level > 0 and d["pct"] > warn - HYSTERESIS_PCT:
            level = alert.level  # not far enough below the threshold yet
        if level > alert.level:
            kind = "kritično" if level == 2 else "upozorenje"
            events.emit(db, "disk_warning", detail=f"{label}: {d['pct']:.0f} % zauzeto, "
                                                    f"slobodno {_gb(d['free'])} ({kind})")
        elif level == 0 and alert.level > 0:
            events.emit(db, "disk_ok", detail=f"{label}: {d['pct']:.0f} % zauzeto, slobodno {_gb(d['free'])}")
        if level != alert.level:
            alert.level, alert.updated_at = level, datetime.now(timezone.utc)
    db.commit()


_last_disk_check = 0.0


def maybe_check_disks(db: Session, every_s: int = 60) -> None:
    global _last_disk_check
    if time.time() - _last_disk_check >= every_s:
        _last_disk_check = time.time()
        check_disks(db)
