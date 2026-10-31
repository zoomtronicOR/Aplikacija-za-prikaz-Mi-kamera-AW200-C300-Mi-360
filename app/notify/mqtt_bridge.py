"""MQTT state for Home Assistant (spec section 6).

Topics, under the configured prefix (default `xiaomi_cam`):
  <prefix>/system/status      online | offline   (retained, offline also as the last will)
  <prefix>/<slug>/status      online | offline   (retained)
  <prefix>/<slug>/motion      ON | OFF           (retained)
  <prefix>/<slug>/event       {"camera", "event", "active", "timestamp"}
With discovery on, every camera appears in Home Assistant as a device with a motion and a
connectivity binary_sensor. No URLs are published.
"""
import json
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt

from app.core.config import get_settings

log = logging.getLogger(__name__)


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
        self._state: dict[str, dict] = {}  # slug -> {"status": ..., "motion": ...}
        self.connected = False
        self.error: str | None = None

    @property
    def prefix(self) -> str:
        return self._cfg.get("prefix") or "xiaomi_cam"

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

    # --- callbacks ---
    def _on_connect(self, client, userdata, flags, reason, props):
        if reason.is_failure:
            self.connected, self.error = False, f"broker je odbio vezu: {reason}"
            return
        self.connected, self.error = True, None
        client.publish(f"{self.prefix}/system/status", "online", qos=1, retain=True)
        self._publish_discovery()
        for slug, st in self._state.items():
            for k, v in st.items():
                client.publish(f"{self.prefix}/{slug}/{k}", v, qos=1, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason, props):
        self.connected = False
        if reason.is_failure:
            self.error = f"veza sa brokerom prekinuta: {reason}"

    # --- state ---
    def set_cameras(self, cameras: list[dict]) -> None:
        """cameras: [{slug, name, model}]; republishes discovery when the list changes."""
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

    def event(self, slug: str, event: str, active: bool | None) -> None:
        if self._client and self.connected:
            # local time with offset, like the spec's example (the container itself runs in UTC)
            now = datetime.now(ZoneInfo(get_settings().timezone))
            payload = {"camera": slug, "event": event, "active": active, "timestamp": now.isoformat(timespec="seconds")}
            self._client.publish(f"{self.prefix}/{slug}/event", json.dumps(payload), qos=1)

    def _publish_discovery(self) -> None:
        if not (self._client and self.connected and self._cfg.get("discovery", True)):
            return
        dp = self._cfg.get("discovery_prefix") or "homeassistant"
        avail = [{"topic": f"{self.prefix}/system/status"}]
        for cam in self._cameras:
            uid = f"xiaomi_cam_hub_{cam['slug']}"
            device = {"identifiers": [uid], "name": cam["name"], "manufacturer": "Xiaomi Camera Hub",
                      "model": cam.get("model") or "kamera"}
            sensors = {
                "motion": {"name": "Pokret", "device_class": "motion",
                           "state_topic": f"{self.prefix}/{cam['slug']}/motion", "payload_on": "ON", "payload_off": "OFF"},
                "connectivity": {"name": "Dostupnost", "device_class": "connectivity",
                                 "state_topic": f"{self.prefix}/{cam['slug']}/status",
                                 "payload_on": "online", "payload_off": "offline"},
            }
            for key, conf in sensors.items():
                conf |= {"unique_id": f"{uid}_{key}", "device": device, "availability": avail}
                self._client.publish(f"{dp}/binary_sensor/{uid}/{key}/config", json.dumps(conf), qos=1, retain=True)
