"""Per-camera recording: ffmpeg segmenter plus grouping of kept segments into clips.

ffmpeg copies go2rtc's RTSP restream into 10 s Matroska segments (video copied, audio re-encoded
to AAC so any camera's audio fits an MP4). Each finished segment is kept if a recording reason
(manual, continuous, schedule) was active while it was written, and contiguous kept segments with
the same reason are joined into clips of up to 5 minutes, ready for upload.
"""
import json
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SEG_SECONDS = 10
CLIP_SECONDS = 300
MAX_GAP_S = 3  # segments further apart than this belong to different clips
MIN_CLIP_S = 2
SEG_PATTERN = "%Y%m%dT%H%M%S"
REASON_PRIORITY = ("manual", "continuous", "schedule")


@dataclass
class Segment:
    path: Path
    start: float
    end: float
    reason: str


@dataclass
class Interval:
    reason: str
    start: float
    end: float | None = None  # None while open

    def overlaps(self, a: float, b: float) -> bool:
        return self.start < b and (self.end is None or self.end > a)


@dataclass
class CameraWorker:
    slug: str
    rtsp_url: str
    buffer_dir: Path
    proc: subprocess.Popen | None = None
    intervals: list[Interval] = field(default_factory=list)
    pending: list[Segment] = field(default_factory=list)
    last_error: str | None = None
    _restart_at: float = 0.0
    _failures: int = 0

    @property
    def seg_dir(self) -> Path:
        return self.buffer_dir / "seg" / self.slug

    @property
    def clip_dir(self) -> Path:
        return self.buffer_dir / "clips"

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # --- reasons -------------------------------------------------------------------------------
    def set_reason(self, reason: str | None, now: float) -> None:
        current = self.intervals[-1] if self.intervals and self.intervals[-1].end is None else None
        if current and current.reason == reason:
            return
        if current:
            current.end = now
        if reason:
            self.intervals.append(Interval(reason, now))

    def _reason_for(self, a: float, b: float) -> str | None:
        reasons = {i.reason for i in self.intervals if i.overlaps(a, b)}
        return next((r for r in REASON_PRIORITY if r in reasons), None)

    def _forget_old_intervals(self, before: float) -> None:
        self.intervals = [i for i in self.intervals if i.end is None or i.end > before]

    # --- ffmpeg segmenter ----------------------------------------------------------------------
    def _start(self, now: float) -> None:
        if now < self._restart_at:
            return
        self.seg_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            # A camera nobody watches is dialed fresh by go2rtc, and its first video packets come
            # without timestamps: with a plain copy the Matroska muxer fails with EINVAL ("Can't write
            # packet with unknown timestamp"). Stamping packets on arrival fixes that; aresample
            # smooths the audio, which arrives in bursts.
            "-use_wallclock_as_timestamps", "1", "-analyzeduration", "5000000", "-probesize", "5000000",
            "-rtsp_transport", "tcp", "-timeout", "10000000", "-i", self.rtsp_url,
            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy",
            "-af", "aresample=async=1000", "-c:a", "aac", "-b:a", "64k",
            "-avoid_negative_ts", "make_zero",
            "-f", "segment", "-segment_time", str(SEG_SECONDS), "-segment_format", "matroska",
            "-reset_timestamps", "1", "-strftime", "1",
            str(self.seg_dir / f"{SEG_PATTERN}.mkv"),
        ]
        # TZ=UTC: segment names are UTC timestamps
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     env={**os.environ, "TZ": "UTC"})
        log.info("%s: recording started", self.slug)

    def _stop(self) -> None:
        if not self.proc:
            return
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)  # lets ffmpeg close the current segment cleanly
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None
        log.info("%s: recording stopped", self.slug)

    def _check_crash(self, now: float) -> None:
        if self.proc and self.proc.poll() is not None:
            err = (self.proc.stderr.read() or b"").decode(errors="replace").strip()[-300:] if self.proc.stderr else ""
            self._failures += 1
            self.last_error = f"ffmpeg je prekinuo snimanje: {err or 'kod ' + str(self.proc.returncode)}"
            log.warning("%s: %s", self.slug, self.last_error)
            self.proc = None
            self._restart_at = now + min(60, 5 * self._failures)

    # --- segments -> clips ---------------------------------------------------------------------
    def _finished_segments(self, now: float) -> list[tuple[Path, float, float]]:
        files = sorted(self.seg_dir.glob("*.mkv")) if self.seg_dir.exists() else []
        if self.running and files:
            files = files[:-1]  # the newest one is still being written
        out = []
        for i, f in enumerate(files):
            try:
                start = datetime.strptime(f.stem, SEG_PATTERN).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                f.unlink(missing_ok=True)
                continue
            if i + 1 < len(files):
                nxt = datetime.strptime(files[i + 1].stem, SEG_PATTERN).replace(tzinfo=timezone.utc).timestamp()
                end = min(nxt, f.stat().st_mtime)
            else:
                end = f.stat().st_mtime
            out.append((f, start, max(end, start + 0.1)))
        return out

    def _flush(self) -> None:
        if not self.pending:
            return
        segs, self.pending = self.pending, []
        if segs[-1].end - segs[0].start < MIN_CLIP_S:  # e.g. the stub left by a crashed ffmpeg
            for s in segs:
                s.path.unlink(missing_ok=True)
            return
        try:
            make_clip(self.slug, segs, self.clip_dir)
            self.last_error = None
        except Exception as e:  # keep going: a broken clip must not stop recording
            self.last_error = f"spajanje snimka nije uspelo: {e}"
            log.exception("%s: clip failed", self.slug)
        for s in segs:
            s.path.unlink(missing_ok=True)

    def process_segments(self, now: float) -> None:
        # pending segments stay on disk until their clip is made; don't take them twice
        queued = {s.path for s in self.pending}
        for path, start, end in self._finished_segments(now):
            if path in queued:
                continue
            reason = self._reason_for(start, end)
            if reason is None:
                path.unlink(missing_ok=True)  # written around a stop, outside any reason
                self._flush()
                continue
            seg = Segment(path, start, end, reason)
            if self.pending:
                first, last = self.pending[0], self.pending[-1]
                if (reason != last.reason or start - last.end > MAX_GAP_S
                        or end - first.start > CLIP_SECONDS + SEG_SECONDS / 2):
                    self._flush()
            self.pending.append(seg)
            if seg.end - self.pending[0].start >= CLIP_SECONDS:
                self._flush()
        if not self.running:
            self._flush()
        self._forget_old_intervals(now - 3600)

    # --- main entry ----------------------------------------------------------------------------
    def tick(self, reason: str | None, now: float) -> None:
        self.set_reason(reason, now)
        self._check_crash(now)
        if reason and not self.running:
            self._start(now)
        elif not reason and self.running:
            self._stop()
        if reason and self.running:
            self._failures = 0 if now - self._restart_at > 120 else self._failures
        self.process_segments(now)

    def shutdown(self) -> None:
        now = time.time()
        self.set_reason(None, now)
        self._stop()
        self.process_segments(now)


def _probe_video_codec(path: Path) -> str:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name", "-of", "json", str(path)], capture_output=True, timeout=30)
    try:
        return json.loads(out.stdout)["streams"][0]["codec_name"]
    except (ValueError, KeyError, IndexError):
        return ""


def make_clip(slug: str, segs: list[Segment], clip_dir: Path) -> Path:
    """Joins segments without re-encoding. MP4 for H.264/H.265 (plays in browsers), MKV otherwise."""
    clip_dir.mkdir(parents=True, exist_ok=True)
    codec = _probe_video_codec(segs[0].path)
    ext = "mp4" if codec in ("h264", "hevc") else "mkv"
    fmt = "%Y%m%dT%H%M%SZ"
    start = datetime.fromtimestamp(segs[0].start, timezone.utc).strftime(fmt)
    end = datetime.fromtimestamp(segs[-1].end, timezone.utc).strftime(fmt)
    final = clip_dir / f"{slug}__{start}__{end}__{segs[0].reason}.{ext}"
    tmp = final.with_name(final.name + ".tmp")
    listing = clip_dir / f".{final.stem}.txt"
    listing.write_text("".join(f"file '{s.path}'\n" for s in segs))
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
           "-i", str(listing), "-map", "0", "-c", "copy"]
    if ext == "mp4":
        cmd += ["-movflags", "+faststart"] + (["-tag:v", "hvc1"] if codec == "hevc" else [])
    cmd += ["-f", "mp4" if ext == "mp4" else "matroska", str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="replace").strip()[-300:])
        tmp.rename(final)  # only complete clips get the final name the uploader looks for
    finally:
        listing.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    log.info("%s: clip %s (%.0f s)", slug, final.name, segs[-1].end - segs[0].start)
    return final


def recover_leftovers(buffer_dir: Path) -> None:
    """Segments left by a crash or restart become one 'recovered' clip per camera (at most one
    clip's worth: segments never wait longer than that). Half-written clips are dropped."""
    clip_dir = buffer_dir / "clips"
    if clip_dir.exists():
        for f in [*clip_dir.glob("*.tmp"), *clip_dir.glob(".*.txt")]:
            f.unlink(missing_ok=True)
    seg_root = buffer_dir / "seg"
    if not seg_root.exists():
        return
    for cam_dir in seg_root.iterdir():
        files = sorted(cam_dir.glob("*.mkv"))
        if not files:
            continue
        segs = []
        for f in files:
            try:
                start = datetime.strptime(f.stem, SEG_PATTERN).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                f.unlink(missing_ok=True)
                continue
            segs.append(Segment(f, start, max(f.stat().st_mtime, start + 0.1), "recovered"))
        try:
            if segs:
                make_clip(cam_dir.name, segs, buffer_dir / "clips")
        except Exception:
            log.exception("%s: recovering leftover segments failed", cam_dir.name)
        for s in segs:
            s.path.unlink(missing_ok=True)
