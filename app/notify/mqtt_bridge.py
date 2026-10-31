"""MQTT for Home Assistant (spec section 6), with discovery and commands.

Topics, under the configured prefix (default `xiaomi_cam`):
  <prefix>/system/status            online | offline   (retained; offline is also the last will)
  <prefix>/<slug>/status            online | offline   (retained)
  <prefix>/<slug>/motion            ON | OFF           (retained)
  <prefix>/<slug>/event             {"camera", "event", "active", "timestamp"}
  <prefix>/<slug>/image             JPEG               (retained; group "image")
  <prefix>/<slug>/state/<key>       control or recording state (retained)
  <prefix>/<slug>/set/<key>         commands from HA   (groups "controls", "recording")
  <prefix>/<slug>/ptz               left | right | up | down (group "ptz")

With discovery on, each camera is a device in Home Assistant. Every optional entity group (image,
controls, recording, ptz) has its own switch in the channel config. A switched-off group's
entities are removed from HA by publishing an empty retained config. No URLs are published.
"""
import asyncio
import json
import logging
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt

from app.core.config import get_settings

log = logging.getLogger(__name__)

GROUPS = ("ha_image", "ha_controls", "ha_recording", "ha_ptz")
REC_MODES = {"off": "Isključeno", "continuous": "Stalno", "schedule": "Po rasporedu", "motion": "Na pokret"}
PTZ_BUTTONS = {"left": ("Okreni levo", "mdi:arrow-left-bold"), "right": ("Okreni desno", "mdi:arrow-right-bold"),
               "up": ("Okreni gore", "mdi:arrow-up-bold"), "down": ("Okreni dole", "mdi:arrow-down-bold")}


class MqttError(Exception):
    pass


def _client(cfg: dict, secret: str | None, client_id: str) -> mqtt.Client:
    c = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if cfg.get("username"):
        c.username_pw_set(cfg["username"], secret or "")
    return c


def test(cfg: dict, secret: str | None) -> None:
    """Connects, publishes a test message and disconnects. Raises MqttError."""
    if not cfg.get("host"):
        raise MqttError("unesi adresu MQTT brokera")
    done, result = threading.Event(), {}

    def on_connect(client, userdata, flags, reason, props):
        result["rc"] = reason
        done.set()

    c = _client(cfg, secret, "xiaomi-cam-hub-test")
    c.on_connect = on_connect
    try:
        c.connect(cfg["host"], int(cfg.get("port") or 1883), keepalive=10)
    except OSError as e:
        raise MqttError(f"broker ne odgovara ({e.__class__.__name__})") from e
    c.loop_start()
    try:
        if not done.wait(8):
            raise MqttError("broker nije odgovorio na prijavu")
        if result["rc"].is_failure:
            raise MqttError("broker je odbio prijavu (korisnik/lozinka?)" if result["rc"].value in (4, 5, 134, 135)
                            else f"broker: {result['rc']}")
        prefix = cfg.get("prefix") or "xiaomi_cam"
        c.publish(f"{prefix}/system/test", json.dumps({"test": True}), qos=1).wait_for_publish(5)
    finally:
        c.loop_stop()
        c.disconnect()


class MqttBridge:
    def __init__(self):
        self._client: mqtt.Client | None = None
        self._key = None
        self._cfg: dict = {}
        self._cameras: list[dict] = []
        self._state: dict[str, dict] = {}       # slug -> {subtopic: value}
        self._published: set[str] = set()       # discovery config topics currently in HA
        self._discovery_lock = threading.Lock()
        self.connected = False
        self.error: str | None = None
        # set by the notifier: commands from HA run on the app's event loop
        self.loop: asyncio.AbstractEventLoop | None = None
        self.on_command: Callable[[str, str, str], Awaitable[None]] | None = None

    @property
    def prefix(self) -> str:
        return self._cfg.get("prefix") or "xiaomi_cam"

    def group(self, name: str) -> bool:
        return bool(self._cfg.get(name, True))

    def configure(self, enabled: bool, cfg: dict, secret: str | None) -> None:
        key = (enabled, json.dumps(cfg, sort_keys=True), secret)
        if key == self._key:
            return
        self._key = key
        self.stop()
        self._cfg = cfg
        if not enabled or not cfg.get("host"):
            return
        c = _client(cfg, secret, "xiaomi-cam-hub")
        c.will_set(f"{self.prefix}/system/status", "offline", qos=1, retain=True)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(2, 60)
        try:
            c.connect_async(cfg["host"], int(cfg.get("port") or 1883), keepalive=30)
        except OSError as e:
            self.error = f"broker ne odgovara ({e.__class__.__name__})"
            return
        c.loop_start()
        self._client = c

    def stop(self) -> None:
        if self._client:
            try:
                self._client.publish(f"{self.prefix}/system/status", "offline", qos=1, retain=True)
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                log.debug("mqtt stop failed", exc_info=True)
        self._client, self.connected = None, False

    # --- callbacks (paho thread) ---
    def _on_connect(self, client, userdata, flags, reason, props):
        if reason.is_failure:
            self.connected, self.error = False, f"broker je odbio vezu: {reason}"
            return
        self.connected, self.error = True, None
        client.publish(f"{self.prefix}/system/status", "online", qos=1, retain=True)
        client.subscribe([(f"{self.prefix}/+/set/+", 1), (f"{self.prefix}/+/ptz", 1),
                          (f"{self._cfg.get('discovery_prefix') or 'homeassistant'}/status", 1)])
        # _published survives reconnects (a config change reconnects), so entities of a group
        # switched off are still removed
        self._publish_discovery()
        for slug, st in self._state.items():
            for k, v in st.items():
                client.publish(f"{self.prefix}/{slug}/{k}", v, qos=1, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason, props):
        self.connected = False
        if reason.is_failure:
            self.error = f"veza sa brokerom prekinuta: {reason}"

    def _on_message(self, client, userdata, msg):
        topic, payload = msg.topic, msg.payload.decode(errors="replace").strip()
        if topic.endswith("/status") and not topic.startswith(self.prefix + "/"):
            if payload == "online":  # Home Assistant restarted: send discovery again
                self._publish_discovery()
            return
        parts = topic[len(self.prefix) + 1:].split("/")
        if len(parts) == 3 and parts[1] == "set":
            slug, key = parts[0], parts[2]
        elif len(parts) == 2 and parts[1] == "ptz":
            slug, key = parts[0], "ptz"
        else:
            return
        if self.on_command and self.loop:
            asyncio.run_coroutine_threadsafe(self.on_command(slug, key, payload), self.loop)

    # --- state ---
    def set_cameras(self, cameras: list[dict]) -> None:
        """cameras: [{slug, name, model, controls, ptz}]; republishes discovery when anything changes."""
        if cameras != self._cameras:
            self._cameras = cameras
            self._publish_discovery()

    def set_state(self, slug: str, key: str, value: str) -> None:
        st = self._state.setdefault(slug, {})
        if st.get(key) == value:
            return
        st[key] = value
        if self._client and self.connected:
            self._client.publish(f"{self.prefix}/{slug}/{key}", value, qos=1, retain=True)

    def forget_state(self, slug: str, key: str) -> None:
        """Forces the next set_state of this key to publish (e.g. to correct HA after a rejected command)."""
        self._state.get(slug, {}).pop(key, None)

    def image(self, slug: str, jpeg: bytes) -> None:
        if self._client and self.connected and self.group("ha_image") and jpeg:
            self._client.publish(f"{self.prefix}/{slug}/image", jpeg, qos=0, retain=True)

    def event(self, slug: str, event: str, active: bool | None) -> None:
        if self._client and self.connected:
            # local time with offset, like the spec's example (the container itself runs in UTC)
            now = datetime.now(ZoneInfo(get_settings().timezone))
            payload = {"camera": slug, "event": event, "active": active, "timestamp": now.isoformat(timespec="seconds")}
            self._client.publish(f"{self.prefix}/{slug}/event", json.dumps(payload), qos=1)

    # --- discovery ---
    def _entities(self, cam: dict) -> list[tuple[str, str, dict, str]]:
        """(component, object id, config, group) for every entity this camera can have."""
        p, s = self.prefix, cam["slug"]
        out = [
            ("binary_sensor", "motion", {"name": "Pokret", "device_class": "motion", "state_topic": f"{p}/{s}/motion",
                                         "payload_on": "ON", "payload_off": "OFF"}, "base"),
            ("binary_sensor", "connectivity", {"name": "Dostupnost", "device_class": "connectivity",
                                               "state_topic": f"{p}/{s}/status",
                                               "payload_on": "online", "payload_off": "offline"}, "base"),
            ("camera", "image", {"name": "Slika", "topic": f"{p}/{s}/image"}, "ha_image"),
            ("switch", "rec_manual", {"name": "Ručno snimanje", "icon": "mdi:record-rec",
                                      "command_topic": f"{p}/{s}/set/rec_manual",
                                      "state_topic": f"{p}/{s}/state/rec_manual"}, "ha_recording"),
            ("select", "rec_mode", {"name": "Način snimanja", "icon": "mdi:video-box",
                                    "options": list(REC_MODES.values()), "command_topic": f"{p}/{s}/set/rec_mode",
                                    "state_topic": f"{p}/{s}/state/rec_mode"}, "ha_recording"),
            ("binary_sensor", "recording", {"name": "Snima se", "device_class": "running",
                                            "state_topic": f"{p}/{s}/state/recording",
                                            "payload_on": "ON", "payload_off": "OFF"}, "ha_recording"),
            ("sensor", "last_motion", {"name": "Poslednji pokret", "device_class": "timestamp",
                                       "state_topic": f"{p}/{s}/state/last_motion"}, "ha_recording"),
        ]
        for c in cam.get("controls", []):
            base = {"name": c["label"], "command_topic": f"{p}/{s}/set/{c['key']}",
                    "state_topic": f"{p}/{s}/state/{c['key']}", "entity_category": "config"}
            if c["kind"] == "bool":
                out.append(("switch", c["key"], base, "ha_controls"))
            else:
                out.append(("select", c["key"], base | {"options": list(c["options"].values())}, "ha_controls"))
        if cam.get("ptz"):
            for d, (name, icon) in PTZ_BUTTONS.items():
                out.append(("button", f"ptz_{d}", {"name": name, "icon": icon, "command_topic": f"{p}/{s}/ptz",
                                                   "payload_press": d}, "ha_ptz"))
        return out

    def _publish_discovery(self) -> None:
        # called from the paho thread (connect, HA restart) and from the notifier
        with self._discovery_lock:
            self._publish_discovery_locked()

    def _publish_discovery_locked(self) -> None:
        if not (self._client and self.connected):
            return
        dp = self._cfg.get("discovery_prefix") or "homeassistant"
        on = self._cfg.get("discovery", True)
        avail = [{"topic": f"{self.prefix}/system/status"}]
        wanted: dict[str, str] = {}
        for cam in self._cameras:
            uid = f"xiaomi_cam_hub_{cam['slug']}"
            device = {"identifiers": [uid], "name": cam["name"], "manufacturer": "Xiaomi Camera Hub",
                      "model": cam.get("model") or "kamera"}
            for component, oid, conf, group in self._entities(cam):
                if on and (group == "base" or self.group(group)):
                    conf = conf | {"unique_id": f"{uid}_{oid}", "device": device, "availability": avail}
                    wanted[f"{dp}/{component}/{uid}/{oid}/config"] = json.dumps(conf)
        for topic, payload in wanted.items():
            self._client.publish(topic, payload, qos=1, retain=True)
        for topic in self._published - set(wanted):
            self._client.publish(topic, "", qos=1, retain=True)  # removes the entity from HA
        self._published = set(wanted)
