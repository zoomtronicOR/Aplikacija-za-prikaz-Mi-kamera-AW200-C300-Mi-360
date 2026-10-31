"""Motion detection on a camera's SD stream, tuned to ignore foliage in the wind.

ffmpeg decodes the stream at FPS frames per second, scaled to W x H grayscale. Per frame:

1. Blur, then compare with a slowly adapting background. A pixel counts as changed when it differs
   by more than max(PIX_MIN, NOISE_K x the median difference). The median follows sensor noise
   (night, rain) but not a moving object, so the threshold adapts to the noise level.
2. The frame is split into 9 x 16 blocks of 10 x 10 px. A block is active when BLOCK_FILL of its
   pixels changed. Most of the frame changing at once (IR switch, lights) resets the background.
3. Blocks the user masked are ignored. With auto-ignore on, blocks that were active much of the
   last minutes (swaying trees, bushes, flags) are suppressed until they calm down. Someone walking
   through a quiet part of the picture still counts.
4. Motion is a connected group of at least `need` counted blocks in CONFIRM_FRAMES frames in a row.
   Isolated flickering blocks don't form a group.

Steps 1 (noise adaptation), 3 (zones, auto-ignore) and 4 (group) each have their own switch in
MotionOptions. With a step off, it falls back to the plain behaviour: a fixed pixel threshold,
no ignored blocks, or a plain count of active blocks.
"""
import logging
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

W, H, FPS = 160, 90, 4
FRAME = W * H
ROWS, COLS, BLOCK = 9, 16, 10
CELLS = ROWS * COLS

BG_ALPHA = 0.08
PIX_MIN, NOISE_K = 12.0, 4.0
PIX_FIXED = 25.0            # pixel threshold with noise adaptation off
BLOCK_FILL = 0.3
LIGHT_CHANGE = 0.5          # share of active blocks treated as a global light change
CONFIRM_FRAMES = 2
# restless-area suppression: activity is an exponential average with a ~90 s time constant
ACTIVITY_ALPHA = 1 / (FPS * 90)
SUPPRESS_ON, SUPPRESS_OFF = 0.12, 0.05


@dataclass(frozen=True)
class MotionOptions:
    sensitivity: int = 50
    mask: str | None = None
    zones_enabled: bool = True
    auto_ignore: bool = True
    noise_adapt: bool = True
    require_group: bool = True


def need_blocks(sensitivity: int) -> int:
    """Blocks a moving object must cover: 1 at sensitivity 100, 4 at 50, 8 at 1."""
    s = min(100, max(1, sensitivity)) / 100
    return max(1, round(1 + 7 * (1 - s)))


def parse_mask(mask: str | None) -> np.ndarray:
    if not mask or len(mask) != CELLS:
        return np.zeros((ROWS, COLS), bool)
    return np.array([c == "1" for c in mask], bool).reshape(ROWS, COLS)


def mask_str(m: np.ndarray) -> str:
    return "".join("1" if v else "0" for v in m.ravel())


def largest_cluster(cells: np.ndarray) -> int:
    """Size of the largest 4-connected group of True cells."""
    seen = np.zeros_like(cells)
    best = 0
    for r0, c0 in zip(*np.nonzero(cells)):
        if seen[r0, c0]:
            continue
        size, stack = 0, [(r0, c0)]
        seen[r0, c0] = True
        while stack:
            r, c = stack.pop()
            size += 1
            for rr, cc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if 0 <= rr < ROWS and 0 <= cc < COLS and cells[rr, cc] and not seen[rr, cc]:
                    seen[rr, cc] = True
                    stack.append((rr, cc))
        best = max(best, size)
    return best


def blur(f: np.ndarray) -> np.ndarray:
    p = np.pad(f, 1, mode="edge")
    return (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:] + p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:]
            + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]) / 9


class MotionDetector:
    def __init__(self, slug: str, rtsp_url: str, options: MotionOptions):
        self.slug, self.rtsp_url = slug, rtsp_url
        self.configure(options)
        # outputs, read by the record loop
        self.level = 0.0            # largest moving group / blocks needed (>= 1 means motion)
        self.live = np.zeros((ROWS, COLS), bool)
        self.suppressed = np.zeros((ROWS, COLS), bool)
        self.error: str | None = None
        self._events: deque[float] = deque(maxlen=1000)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, name=f"motion-{slug}", daemon=True)
        self._thread.start()

    def configure(self, options: MotionOptions) -> None:
        # one assignment, so the analysis thread never sees half-applied options
        self.options = options
        self.mask = parse_mask(options.mask) if options.zones_enabled else np.zeros((ROWS, COLS), bool)

    def drain(self) -> list[float]:
        """Motion timestamps (epoch seconds) since the last call."""
        with self._lock:
            out = list(self._events)
            self._events.clear()
        return out

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
        self._thread.join(5)

    def _spawn(self) -> subprocess.Popen:
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
               "-rtsp_transport", "tcp", "-timeout", "10000000", "-i", self.rtsp_url, "-an",
               "-vf", f"fps={FPS},scale={W}:{H}:flags=area,format=gray",
               "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy())

    def _run(self) -> None:
        backoff = 5
        while not self._stop.is_set():
            self._proc = self._spawn()
            started = time.time()
            try:
                self._analyse(self._proc.stdout)
            except Exception:
                log.exception("%s: motion analysis failed", self.slug)
            if self._stop.is_set():
                break
            err = (self._proc.stderr.read() or b"").decode(errors="replace").strip()[-200:] if self._proc.stderr else ""
            self._proc.kill()
            self._proc.wait()
            self.error = f"detekcija pokreta je prekinuta: {err or 'stream se zatvorio'}"
            log.warning("%s: %s", self.slug, self.error)
            backoff = 5 if time.time() - started > 120 else min(60, backoff * 2)
            self._stop.wait(backoff)

    def _analyse(self, pipe) -> None:
        bg = None
        streak = 0
        activity = np.zeros((ROWS, COLS), np.float32)
        suppressed = np.zeros((ROWS, COLS), bool)
        warmup = FPS * 3  # the first frames before a key frame are grey; let the background settle
        while not self._stop.is_set():
            buf = pipe.read(FRAME)
            if len(buf) < FRAME:
                return
            frame = blur(np.frombuffer(buf, dtype=np.uint8).reshape(H, W).astype(np.float32))
            self.error = None
            if bg is None or warmup > 0:
                bg = frame if bg is None else bg + 0.5 * (frame - bg)
                warmup -= 1
                continue

            opts = self.options
            diff = np.abs(frame - bg)
            pix_t = max(PIX_MIN, NOISE_K * float(np.median(diff))) if opts.noise_adapt else PIX_FIXED
            active = (diff > pix_t).reshape(ROWS, BLOCK, COLS, BLOCK).mean(axis=(1, 3)) > BLOCK_FILL
            if active.mean() > LIGHT_CHANGE:
                bg, streak = frame, 0  # the whole picture changed: light, not motion
                continue
            bg += BG_ALPHA * (frame - bg)

            activity += ACTIVITY_ALPHA * (active.astype(np.float32) - activity)
            if opts.auto_ignore:
                suppressed = (suppressed & (activity > SUPPRESS_OFF)) | (activity > SUPPRESS_ON)
            else:
                suppressed[:] = False

            counted = active & ~self.mask & ~suppressed
            group = largest_cluster(counted) if opts.require_group else int(counted.sum())
            need = need_blocks(opts.sensitivity)
            self.level = group / need
            self.live, self.suppressed = active, suppressed.copy()

            streak = streak + 1 if group >= need else 0
            if streak >= CONFIRM_FRAMES:
                with self._lock:
                    self._events.append(time.time())
