"""Object detection (YOLOX-tiny, ONNX, CPU) to confirm that a motion candidate is a person, vehicle or animal.

One shared worker thread runs the model for all cameras. A request carries the exact frame on which
the motion detector saw movement, plus the blocks that moved. A detection only counts when its
box overlaps those blocks, so a parked car or a pedestrian in an ignored zone doesn't confirm
movement that came from a tree.

YOLOX ONNX contract (Megvii demo): input "images" 1x3x416x416, BGR, letterboxed with 114, values
0..255. The output has 3549 rows of [cx, cy, w, h, objectness, 80 COCO class scores] relative to
grids with strides 8/16/32.
"""
import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

SIZE = 416
STRIDES = (8, 16, 32)
GROUPS = {
    "person": {0: "osoba"},
    "vehicle": {1: "bicikl", 2: "automobil", 3: "motor", 5: "autobus", 7: "kamion"},
    "animal": {14: "ptica", 15: "mačka", 16: "pas", 17: "konj", 18: "ovca", 19: "krava"},
}


@dataclass
class Request:
    slug: str
    frame: np.ndarray          # H x W x 3 RGB uint8
    moving: np.ndarray         # ROWS x COLS bool: blocks that moved
    groups: tuple[str, ...]    # e.g. ("person", "vehicle")
    min_score: float
    done: Callable[[str | None], None]  # label like "osoba 87%", or None when nothing qualifies


def _grids() -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for s in STRIDES:
        n = SIZE // s
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((n * n, 1), s))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


class ObjectDetector:
    def __init__(self, model_path: str):
        import onnxruntime as ort  # imported lazily: only needed when AI verification is used
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2  # leave cores for ffmpeg, go2rtc and the app
        self.session = ort.InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        self.grids, self.strides = _grids()

    def detect(self, rgb: np.ndarray) -> list[tuple[int, float, tuple[float, float, float, float]]]:
        """[(class id, score, (x1, y1, x2, y2) in frame pixels)] with score >= 0.25."""
        h, w = rgb.shape[:2]
        r = min(SIZE / h, SIZE / w)
        nh, nw = int(h * r), int(w * r)
        # nearest-neighbour resize is plenty for detection and needs no image library
        ys = (np.arange(nh) / r).astype(int).clip(0, h - 1)
        xs = (np.arange(nw) / r).astype(int).clip(0, w - 1)
        canvas = np.full((SIZE, SIZE, 3), 114, np.uint8)
        canvas[:nh, :nw] = rgb[ys][:, xs][:, :, ::-1]  # RGB -> BGR, as the model was trained
        blob = canvas.transpose(2, 0, 1)[None].astype(np.float32)

        out = self.session.run(None, {self.input: blob})[0][0]
        out[:, :2] = (out[:, :2] + self.grids) * self.strides
        out[:, 2:4] = np.exp(out[:, 2:4]) * self.strides
        scores = out[:, 4:5] * out[:, 5:]
        cls = scores.argmax(1)
        best = scores[np.arange(len(cls)), cls]
        keep = best >= 0.25
        res = []
        for (cx, cy, bw, bh), c, s in zip(out[keep, :4], cls[keep], best[keep]):
            res.append((int(c), float(s), ((cx - bw / 2) / r, (cy - bh / 2) / r, (cx + bw / 2) / r, (cy + bh / 2) / r)))
        return res


def _overlaps(box: tuple[float, float, float, float], moving: np.ndarray, w: int, h: int) -> bool:
    rows, cols = moving.shape
    x1, y1, x2, y2 = box
    c1, c2 = int(max(0, x1) / w * cols), int(min(w - 1, x2) / w * cols)
    r1, r2 = int(max(0, y1) / h * rows), int(min(h - 1, y2) / h * rows)
    return bool(moving[r1:r2 + 1, c1:c2 + 1].any())


class AiWorker:
    """Single background thread; requests for a camera already waiting are dropped."""

    def __init__(self, model_path: str):
        self.model_path = model_path
        self.error: str | None = None
        self.last_ms: float | None = None
        self._q: queue.Queue[Request] = queue.Queue(maxsize=8)
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._detector: ObjectDetector | None = None
        threading.Thread(target=self._run, name="ai", daemon=True).start()

    @property
    def available(self) -> bool:
        return os.path.exists(self.model_path)

    def submit(self, req: Request) -> bool:
        with self._lock:
            if req.slug in self._pending:
                return False
            try:
                self._q.put_nowait(req)
            except queue.Full:
                return False
            self._pending.add(req.slug)
            return True

    def _run(self) -> None:
        while True:
            req = self._q.get()
            label = None
            try:
                if self._detector is None:
                    self._detector = ObjectDetector(self.model_path)
                t = time.perf_counter()
                found = self._detector.detect(req.frame)
                self.last_ms = (time.perf_counter() - t) * 1000
                h, w = req.frame.shape[:2]
                names = {cid: name for g in req.groups for cid, name in GROUPS[g].items()}
                hits = [(s, names[c]) for c, s, box in found
                        if c in names and s >= req.min_score and _overlaps(box, req.moving, w, h)]
                if hits:
                    s, name = max(hits)
                    label = f"{name} {round(s * 100)}%"
                self.error = None
            except Exception as e:
                self.error = f"AI provera nije uspela: {e}"
                log.exception("ai detection failed")
            finally:
                with self._lock:
                    self._pending.discard(req.slug)
            try:
                req.done(label)
            except Exception:
                log.exception("ai callback failed")
