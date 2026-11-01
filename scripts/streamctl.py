#!/usr/bin/env python3
"""Reconnects a camera's ffmpeg source in go2rtc on request from the app.

Runs directly in the LXC (systemd unit xiaomi-cam-hub-streamctl.service), because the app container has
no access to Docker. The app drops an empty file data/streamctl/<slug>.restart; this helper looks up that
stream's ffmpeg HTTP sources in config/go2rtc.yaml itself and kills only those ffmpeg processes inside the
go2rtc container. go2rtc then reopens the source while its consumers stay connected.

Needed for the ESP32-CAM: after a resolution change it keeps sending the new frame size over the old
connection with the old header, which shows as a broken picture. Only a new connection is clean.
Re-PUTting or deleting the stream in go2rtc is not an option: it leaves the old ffmpeg running.
"""
import os
import re
import subprocess
import time

APP_DIR = os.environ.get("APP_DIR", "/opt/xiaomi-cam-hub")
REQ_DIR = os.path.join(APP_DIR, "data/streamctl")
GO2RTC_YAML = os.path.join(APP_DIR, "config/go2rtc.yaml")
CONTAINER = "xiaomi-go2rtc"
SLUG_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


def ffmpeg_urls(slug: str) -> list[str]:
    """http(s) inputs of the stream's ffmpeg: sources, as written in go2rtc.yaml."""
    with open(GO2RTC_YAML) as f:
        text = f.read()
    m = re.search(rf"^  {re.escape(slug)}:\n((?:    .*\n)+)", text, re.M)
    if not m:
        return []
    return re.findall(r"^    - ffmpeg:(https?://[^#\s]+)", m.group(1), re.M)


def restart(slug: str) -> None:
    for url in ffmpeg_urls(slug):
        # pkill -f takes a regex; match the exact input argument
        pattern = "-i " + re.escape(url) + "( |$)"
        subprocess.run(["docker", "exec", CONTAINER, "pkill", "-f", "--", pattern],
                       capture_output=True, timeout=10)
        print(f"{slug}: reconnected {url.split('@')[-1]}", flush=True)


def main() -> None:
    os.makedirs(REQ_DIR, exist_ok=True)
    os.chown(REQ_DIR, 10001, 10001)  # the app runs as uid 10001
    while True:
        for name in os.listdir(REQ_DIR):
            path = os.path.join(REQ_DIR, name)
            slug = name.removesuffix(".restart")
            try:
                os.remove(path)
            except OSError:
                continue
            if name.endswith(".restart") and SLUG_RE.match(slug):
                try:
                    restart(slug)
                except Exception as e:  # keep serving later requests
                    print(f"{slug}: {e}", flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
