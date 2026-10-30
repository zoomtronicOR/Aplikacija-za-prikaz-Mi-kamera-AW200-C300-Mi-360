"""go2rtc API client and validation of user-supplied camera sources."""
import base64
import re
import struct
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import get_settings

# go2rtc also accepts sources such as exec:, echo:, expr: and ffmpeg:device that run commands or touch
# local devices; only plain network stream URLs are accepted from users.
ALLOWED_SCHEMES = {"rtsp", "rtsps", "rtmp", "http", "https", "onvif"}
SAFE_URL = re.compile(r"^[^\s#\"'`$\\]+$")


class SourceError(ValueError):
    pass


def validate_source(url: str) -> str:
    url = url.strip()
    if not SAFE_URL.match(url):
        raise SourceError("Adresa sadrži nedozvoljene znakove (razmak, #, navodnike…)")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise SourceError(f"Dozvoljene vrste adresa: {', '.join(sorted(ALLOWED_SCHEMES))}://")
    if not parts.hostname:
        raise SourceError("Adresa nema naziv ili IP adresu uređaja")
    return url


def mask_source(url: str) -> str:
    """Hides the password (user:pass@) and common password query params."""
    parts = urlsplit(url)
    netloc = parts.netloc
    if parts.password:
        netloc = netloc.replace(f":{parts.password}@", ":***@", 1)
    query = re.sub(r"(?i)\b(password|pass|pwd|token|key)=[^&]*", r"\1=***", parts.query)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def host_port(url: str) -> tuple[str, int] | None:
    parts = urlsplit(url)
    if not parts.hostname:
        return None
    default = {"rtsp": 554, "rtsps": 322, "rtmp": 1935, "http": 80, "https": 443, "onvif": 80}
    return parts.hostname, parts.port or default.get(parts.scheme.lower(), 80)


# direct: go2rtc reads the URL itself (RTSP/ONVIF cameras with H.264/H.265)
# h264:   ffmpeg reads it and encodes H.264 (codecs browsers can't play)
# mjpeg:  multipart MJPEG over HTTP such as ESP32-CAM. go2rtc's own parser rejects its framing
#         ("wrong boundary") and leaks the TCP connection on each retry, and ffmpeg's autodetect
#         fails, so ffmpeg is forced to the mjpeg demuxer (`mjpeg_http` input template in go2rtc.yaml)
#         and only copies the JPEG frames (no CPU). The browser player falls back to its mjpeg mode.
MODES = ("direct", "h264", "mjpeg")

# Never re-PUT or DELETE a running ffmpeg stream to "restart" it: go2rtc keeps the old ffmpeg
# alive, and a single-client source like the ESP32 then refuses the new one.


def source_for(url: str, mode: str) -> str:
    # the user URL is validated to contain no '#', so it can't inject ffmpeg options
    if mode == "direct":
        return url
    if mode == "mjpeg":
        return f"ffmpeg:{url}#input=mjpeg_http#video=copy"
    return f"ffmpeg:{url}#video=h264"


def build_sources(url: str, mode: str) -> list[str]:
    return [source_for(url, mode)]


def probe_source(url: str, mode: str) -> str:
    # h264 encoding just to grab one JPEG is wasteful; mjpeg is the cheapest ffmpeg output
    return f"ffmpeg:{url}#video=mjpeg" if mode == "h264" else source_for(url, mode)


def _client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(base_url=s.go2rtc_url, auth=(s.go2rtc_user, s.go2rtc_password), timeout=45)


def auth_header() -> str:
    s = get_settings()
    return "Basic " + base64.b64encode(f"{s.go2rtc_user}:{s.go2rtc_password}".encode()).decode()


async def put_stream(name: str, sources: list[str]) -> None:
    """Creates or replaces a stream; go2rtc also writes it to go2rtc.yaml."""
    async with _client() as c:
        r = await c.put("/api/streams", params=[("name", name)] + [("src", s) for s in sources])
        r.raise_for_status()


async def delete_stream(name: str) -> None:
    async with _client() as c:
        r = await c.delete("/api/streams", params={"src": name})
        if r.status_code not in (200, 404):
            r.raise_for_status()


def _jpeg_size(data: bytes) -> str | None:
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        if data[i + 1] in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return f"{w}x{h}"
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


async def probe(src: str) -> str:
    """Grabs one frame from a source (stream name or URL). Returns the resolution, raises SourceError."""
    async with _client() as c:
        try:
            r = await c.get("/api/frame.jpeg", params={"src": src})
        except httpx.TimeoutException:
            raise SourceError("Kamera nije poslala sliku u roku od 45 s")
    if r.status_code != 200 or not r.content:
        raise SourceError("Kamera nije poslala sliku. Proveri adresu, korisničko ime i lozinku.")
    return _jpeg_size(r.content) or "nepoznata rezolucija"


async def motor(stream: str, operation: int) -> httpx.Response:
    async with _client() as c:
        return await c.post("/api/xiaomi/motor", params={"src": stream, "operation": operation})
