"""Builds camera controls for Xiaomi models the app doesn't know yet, from their public MIoT spec.

Standard camera properties (camera-control and indicator-light services) are mapped to the
app's normalized controls. The result is stored per camera as JSON and verified on the device
before saving: only properties the camera actually answers are kept.
"""
import httpx

BASE = "https://miot-spec.org/miot-spec-v2/"

# (service, property) -> control key; value encoding is read from the spec
PROPERTY_MAP = {
    ("camera-control", "on"): "power",
    ("camera-control", "image-rollover"): "flip",
    ("camera-control", "night-shot"): "night_mode",
    ("camera-control", "time-watermark"): "watermark",
    ("camera-control", "wdr-mode"): "wdr",
    ("camera-control", "glimmer-full-color"): "full_color",
    ("camera-control", "motion-tracking"): "tracking",
    ("indicator-light", "on"): "light",
}
NIGHT_WORDS = {"auto": "auto", "on": "on", "open": "on", "off": "off", "close": "off"}


def _short(urn: str) -> str:
    return urn.split(":")[3]


def fetch_spec(model: str) -> dict | None:
    with httpx.Client(timeout=20) as c:
        instances = c.get(BASE + "instances", params={"status": "all"}).json()["instances"]
        versions = sorted((i for i in instances if i["model"] == model), key=lambda i: i["version"])
        if not versions:
            return None
        return c.get(BASE + "instance", params={"type": versions[-1]["type"]}).json()


def controls_from_spec(spec: dict) -> dict:
    """{"props": {key: [siid, piid, codec, enum map]}} for the writable standard properties."""
    props = {}
    for svc in spec.get("services", []):
        for p in svc.get("properties", []):
            key = PROPERTY_MAP.get((_short(svc["type"]), _short(p["type"])))
            access = p.get("access", [])
            if not key or key in props or "write" not in access or "read" not in access:
                continue
            if key == "night_mode":
                mapping = {}
                for v in p.get("value-list", []):
                    word = NIGHT_WORDS.get(v["description"].strip().lower())
                    if word and word not in mapping:
                        mapping[word] = v["value"]
                if len(mapping) < 2:
                    continue
                props[key] = [svc["iid"], p["iid"], "enum", mapping]
            elif key == "flip" and p.get("format") != "bool":
                props[key] = [svc["iid"], p["iid"], "flip", None]  # 0 / 180 degrees
            elif p.get("format") == "bool":
                props[key] = [svc["iid"], p["iid"], "bool", None]
    return {"props": props}
