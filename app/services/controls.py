"""Per-model camera control drivers.

Every model exposes normalized controls (power, night_mode, ...) so the UI never sees model-specific
property ids or value encodings. Xiaomi drivers talk miIO: property ids come from each model's MIoT
spec (miot-spec.org), and the legacy chuangmi command names match what the device answers to
get_prop. The ESP32 driver talks to Espressif's CameraWebServer over HTTP (/status, /control).
"""
import gzip
import json
import re
from dataclasses import dataclass, field

import httpx

from app.core.config import get_settings
from app.models import Camera
from app.services.miio import MiioDevice, MiioError


class ControlError(Exception):
    pass


@dataclass(frozen=True)
class Control:
    key: str
    label: str
    kind: str  # "bool" | "enum"
    options: dict[str, str] | None = None
    # the change only shows after the video stream reconnects (the UI reconnects its player)
    restarts_stream: bool = False

    def coerce(self, value):
        """Validates a value coming from the API."""
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError("očekivana je vrednost true/false")
            return value
        if value not in (self.options or {}):
            raise ValueError(f"dozvoljene vrednosti: {', '.join(self.options or {})}")
        return value


NIGHT_OPTIONS = {"auto": "Automatski", "on": "Uključen", "off": "Isključen"}

XIAOMI_CONTROLS = {
    c.key: c for c in (
        Control("power", "Kamera uključena", "bool"),
        Control("night_mode", "Noćni režim", "enum", NIGHT_OPTIONS),
        Control("flip", "Okreni sliku 180°", "bool"),
        Control("watermark", "Vremenski žig", "bool"),
        Control("light", "Lampica statusa", "bool"),
        Control("wdr", "WDR", "bool"),
        Control("full_color", "Kolor noću", "bool"),
        Control("tracking", "Praćenje pokreta", "bool"),
    )
}

ACTION_LABELS = {"restart": "Restart kamere", "ptz_calibrate": "Kalibracija okretanja"}


# value codecs: (to_device, from_device)
BOOL = (lambda v: bool(v), lambda v: bool(v))
FLIP = (lambda v: 180 if v else 0, lambda v: v == 180)
ON_OFF = (lambda v: "on" if v else "off", lambda v: v == "on")


def enum(mapping: dict[str, int | str]):
    reverse = {str(v): k for k, v in mapping.items()}
    return (lambda v: mapping[v], lambda v: reverse.get(str(v)))


class DeviceRegistry:
    """Caches MiioDevice objects; tokens are read from the protected tokens file, never from the DB."""

    def __init__(self):
        self._devices: dict[str, MiioDevice] = {}

    def get(self, did: str, ip: str) -> MiioDevice:
        dev = self._devices.get(did)
        if dev is None or dev.ip != ip:
            with open(get_settings().miio_tokens_file) as f:
                token = json.load(f)[did]["token"]
            dev = self._devices[did] = MiioDevice(ip, token)
        return dev


devices = DeviceRegistry()


async def _miio(cam: Camera, method: str, params):
    if not cam.did or not cam.ip:
        raise ControlError("kamera nema Xiaomi podatke (DID/IP)")
    try:
        return await devices.get(cam.did, cam.ip).send(method, params)
    except MiioError as e:
        raise ControlError(str(e)) from e


@dataclass
class MiotModel:
    """MIoT models: properties addressed by (siid, piid), actions by (siid, aiid)."""
    props: dict[str, tuple[int, int, tuple]]
    actions: dict[str, tuple[int, int]] = field(default_factory=dict)
    # pan/tilt goes over the go2rtc video session (MISS motor command), not miIO
    ptz: bool = False

    @property
    def controls(self) -> dict[str, Control]:
        return {k: XIAOMI_CONTROLS[k] for k in self.props}

    async def get_state(self, cam: Camera) -> dict:
        keys = list(self.props)
        res = await _miio(cam, "get_properties",
                          [{"did": cam.did, "siid": self.props[k][0], "piid": self.props[k][1]} for k in keys])
        # C300 omits "code" on success; a property without "value" is unsupported or failed
        return {k: self.props[k][2][1](r["value"]) for k, r in zip(keys, res) if "value" in r}

    async def set(self, cam: Camera, key: str, value) -> None:
        siid, piid, codec = self.props[key]
        res = await _miio(cam, "set_properties", [{"did": cam.did, "siid": siid, "piid": piid, "value": codec[0](value)}])
        if res[0].get("code", 0) != 0:
            raise ControlError(f"kamera je odbila izmenu (kod {res[0].get('code')})")

    async def action(self, cam: Camera, name: str) -> None:
        siid, aiid = self.actions[name]
        res = await _miio(cam, "action", {"did": cam.did, "siid": siid, "aiid": aiid, "in": []})
        if isinstance(res, dict) and res.get("code") not in (0, None):
            raise ControlError(f"kamera je odbila radnju (kod {res.get('code')})")


@dataclass
class LegacyModel:
    """Older chuangmi cameras: get_prop with names, set_<name> commands."""
    props: dict[str, tuple[str, tuple]]
    actions: dict[str, str] = field(default_factory=dict)
    ptz: bool = False

    @property
    def controls(self) -> dict[str, Control]:
        return {k: XIAOMI_CONTROLS[k] for k in self.props}

    async def get_state(self, cam: Camera) -> dict:
        keys = list(self.props)
        res = await _miio(cam, "get_prop", [self.props[k][0] for k in keys])
        return {k: self.props[k][1][1](v) for k, v in zip(keys, res)}

    async def set(self, cam: Camera, key: str, value) -> None:
        name, codec = self.props[key]
        res = await _miio(cam, f"set_{name}", [codec[0](value)])
        if not (isinstance(res, list) and [str(r).lower() for r in res] == ["ok"]):  # ipc019 answers ["OK"]
            raise ControlError(f"kamera je odbila izmenu ({res})")

    async def action(self, cam: Camera, name: str) -> None:
        raise ControlError("radnja nije podržana")


LEVELS = {"-2": "-2", "-1": "-1", "0": "0", "1": "+1", "2": "+2"}
# JPEG quality: lower number = better image, bigger frames
ESP32_QUALITY = {"10": "Najbolji", "15": "Vrlo dobar", "20": "Dobar", "30": "Srednji", "45": "Nizak"}
# the framesize enum changed between esp32-camera releases; detect_esp32 picks the right table
ESP32_FRAMESIZES = {
    "v1": {"10": "1600×1200", "9": "1280×1024", "8": "1024×768", "7": "800×600", "6": "640×480",
           "5": "400×296", "4": "320×240", "3": "240×176", "0": "160×120"},
    "v2": {"13": "1600×1200", "12": "1280×1024", "11": "1280×720", "10": "1024×768", "9": "800×600",
           "8": "640×480", "6": "400×296", "5": "320×240", "3": "240×176", "1": "160×120"},
}


@dataclass
class Esp32Model:
    """Espressif CameraWebServer: GET /status returns all sensor values, GET /control?var=&val= sets one."""
    framesizes: dict[str, str]
    actions: dict[str, str] = field(default_factory=dict)
    ptz: bool = False

    @property
    def controls(self) -> dict[str, Control]:
        return {c.key: c for c in (
            # CameraWebServer applies a new frame size to /stream only on a new connection
            Control("framesize", "Rezolucija", "enum", self.framesizes, restarts_stream=True),
            Control("quality", "Kvalitet slike", "enum", ESP32_QUALITY),
            Control("brightness", "Osvetljenost", "enum", LEVELS),
            Control("contrast", "Kontrast", "enum", LEVELS),
            Control("saturation", "Zasićenost boja", "enum", LEVELS),
            Control("vflip", "Okreni vertikalno", "bool"),
            Control("hmirror", "Ogledalo (horizontalno)", "bool"),
            Control("awb", "Automatski balans bele", "bool"),
            Control("aec", "Automatska ekspozicija", "bool"),
            Control("agc", "Automatsko pojačanje", "bool"),
        )}

    async def _get(self, cam: Camera, path: str, **params) -> httpx.Response:
        if not cam.ip:
            raise ControlError("kamera nema IP adresu")
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"http://{cam.ip}{path}", params=params)
        except httpx.HTTPError as e:
            raise ControlError(f"kamera ne odgovara ({e.__class__.__name__})") from e
        if r.status_code != 200:
            raise ControlError(f"kamera je vratila grešku {r.status_code}")
        return r

    async def get_state(self, cam: Camera) -> dict:
        status = (await self._get(cam, "/status")).json()
        out = {}
        for key, c in self.controls.items():
            if key in status:
                out[key] = bool(status[key]) if c.kind == "bool" else str(status[key])
        return out

    async def set(self, cam: Camera, key: str, value) -> None:
        await self._get(cam, "/control", var=key, val=int(value))  # enums are numeric strings, bools 0/1

    async def action(self, cam: Camera, name: str) -> None:
        raise ControlError("radnja nije podržana")


MODELS: dict[str, MiotModel | LegacyModel | Esp32Model] = {
    # Xiaomi Outdoor Camera AW200
    "isa.camera.hlc9a": MiotModel(
        props={
            "power": (2, 1, BOOL),
            "flip": (2, 2, FLIP),
            "night_mode": (2, 3, enum({"off": 0, "on": 1, "auto": 2})),
            "watermark": (2, 4, BOOL),
            "light": (3, 1, BOOL),
        },
        actions={"restart": (6, 1)},
    ),
    # Xiaomi Smart Camera C300 (note the inverted night-shot encoding vs. AW200)
    "xiaomi.camera.c01a01": MiotModel(
        props={
            "power": (2, 1, BOOL),
            "flip": (2, 2, FLIP),
            "night_mode": (2, 3, enum({"on": 0, "off": 1, "auto": 2})),
            "wdr": (2, 5, BOOL),
            "full_color": (2, 6, BOOL),
            "tracking": (2, 8, BOOL),
            "watermark": (2, 9, BOOL),
            "light": (3, 1, BOOL),
            # speaker mute (11, 2) is in the spec but the device returns no value locally
        },
        actions={"ptz_calibrate": (2, 1)},
        ptz=True,
    ),
    # Mi 360° 1080p (chuangmi.camera.ipc019): rejects MIoT, answers legacy miIO only
    "chuangmi.camera.ipc019": LegacyModel(
        props={
            "power": ("power", ON_OFF),
            "night_mode": ("night_mode", enum({"auto": 0, "off": 1, "on": 2})),
            "flip": ("flip", ON_OFF),
            "watermark": ("watermark", ON_OFF),
            "light": ("light", ON_OFF),
            "wdr": ("wdr", ON_OFF),
            "full_color": ("full_color", ON_OFF),
            "tracking": ("track", ON_OFF),
        },
        ptz=True,
    ),
    "esp32-camerawebserver-v1": Esp32Model(ESP32_FRAMESIZES["v1"]),
    "esp32-camerawebserver-v2": Esp32Model(ESP32_FRAMESIZES["v2"]),
}


def capabilities(model: str | None) -> dict:
    m = MODELS.get(model or "")
    if m is None:
        return {"controls": [], "actions": [], "ptz": False}
    return {
        "controls": [c.__dict__ for c in m.controls.values()],
        "actions": [{"key": a, "label": ACTION_LABELS[a]} for a in m.actions],
        "ptz": m.ptz,
    }


async def detect_esp32(host: str) -> str | None:
    """Returns the ESP32 model id if host runs Espressif's CameraWebServer, else None.

    The framesize numbering differs between esp32-camera versions, so it is read from the
    <select id="framesize"> on the device's own page: in v1, value 9 is SXGA.
    """
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            status = (await c.get(f"http://{host}/status")).json()
            if "framesize" not in status:
                return None
            page = (await c.get(f"http://{host}/")).content
    except (httpx.HTTPError, ValueError):
        return None
    if page[:2] == b"\x1f\x8b":  # served gzipped without Content-Encoding handling
        page = gzip.decompress(page)
    m = re.search(rb'<option[^>]*value="?9"?[^>]*>([^<]*)', page)
    return "esp32-camerawebserver-v1" if m and b"SXGA" in m.group(1) else "esp32-camerawebserver-v2"
