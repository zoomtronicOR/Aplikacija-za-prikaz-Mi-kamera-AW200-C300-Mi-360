#!/usr/bin/env python3
"""CT-wide CPU/RAM/load/uptime for the system page, written every 15 s to data/hoststats/stats.json.

Runs directly in the LXC (systemd unit xiaomi-cam-hub-stats.service), not in Docker. lxcfs answers
/proc reads relative to the reader's cgroup, so only a process at the CT level sees the CT's own
totals; inside a Docker container the same files describe just that container.
"""
import json
import os
import time

OUT_DIR = os.environ.get("STATS_DIR", "/opt/xiaomi-cam-hub/data/hoststats")
EVERY_S = 15


def cpu_times() -> tuple[int, int]:
    with open("/proc/stat") as f:
        vals = [int(v) for v in f.readline().split()[1:]]
    return sum(vals), vals[3] + (vals[4] if len(vals) > 4 else 0)


def meminfo() -> dict:
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0]) * 1024
    return info


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    prev = cpu_times()
    while True:
        time.sleep(EVERY_S)
        now = cpu_times()
        busy = 1 - (now[1] - prev[1]) / max(1, now[0] - prev[0])
        prev = now
        mem = meminfo()
        with open("/proc/loadavg") as f:
            load = [float(x) for x in f.read().split()[:3]]
        with open("/proc/uptime") as f:
            uptime = int(float(f.read().split()[0]))
        stats = {"ts": time.time(), "cpu_pct": round(100 * busy, 1), "cpus": os.cpu_count(), "load": load,
                 "mem_total": mem["MemTotal"], "mem_available": mem.get("MemAvailable", mem.get("MemFree", 0)),
                 "uptime_s": uptime}
        tmp = os.path.join(OUT_DIR, ".stats.json.tmp")
        with open(tmp, "w") as f:
            json.dump(stats, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(OUT_DIR, "stats.json"))


if __name__ == "__main__":
    main()
