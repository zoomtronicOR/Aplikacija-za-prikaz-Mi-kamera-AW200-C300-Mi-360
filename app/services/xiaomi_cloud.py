"""Xiaomi cloud client reusing the login go2rtc already holds.

A port of go2rtc's pkg/xiaomi/cloud.go (LoginWithToken + signed, RC4-encrypted requests). After the
Xiaomi login in go2rtc, the account's passToken sits in go2rtc.yaml under `xiaomi:`; the app reads
it through go2rtc's /api/config, so it never has to ask for the Xiaomi password. Device tokens
from the device list are secrets: never log or return them.
"""
import base64
import hashlib
import json
import os
import struct
import time

import httpx
import yaml
from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
from cryptography.hazmat.primitives.ciphers import Cipher

from app.core.config import get_settings

SID = "xiaomiio"  # the Mi Home app's service id, the same one go2rtc logs in with
LOGIN_URL = "https://account.xiaomi.com/pass/serviceLogin?_json=true&sid=" + SID


class CloudError(Exception):
    pass


def base_url(region: str) -> str:
    return f"https://{region}.api.io.mi.com/app" if region in ("de", "i2", "ru", "sg", "us") else "https://api.io.mi.com/app"


def go2rtc_accounts() -> dict[str, str]:
    """{user id: passToken} from go2rtc's config."""
    s = get_settings()
    r = httpx.get(f"{s.go2rtc_url}/api/config", auth=(s.go2rtc_user, s.go2rtc_password), timeout=10)
    r.raise_for_status()
    cfg = yaml.safe_load(r.text) or {}
    return {str(k): str(v) for k, v in (cfg.get("xiaomi") or {}).items()}


def _rc4(key: bytes, data: bytes) -> bytes:
    enc = Cipher(ARC4(key), mode=None).encryptor()
    enc.update(bytes(1024))  # Xiaomi's RC4 drops the first 1024 bytes of keystream
    return enc.update(data)


def _signature(method: str, path: str, data: str, rc4_hash: str | None, signed_nonce: bytes) -> str:
    s = f"{method}&{path}&data={data}"
    if rc4_hash is not None:
        s += f"&rc4_hash__={rc4_hash}"
    s += "&" + base64.b64encode(signed_nonce).decode()
    return base64.b64encode(hashlib.sha1(s.encode()).digest()).decode()


class Cloud:
    def __init__(self, user_id: str, pass_token: str):
        self.client = httpx.Client(timeout=15, follow_redirects=True)
        r = self.client.get(LOGIN_URL, headers={"Cookie": f"userId={user_id}; passToken={pass_token}"})
        body = r.text
        if not body.startswith("&&&START&&&"):
            raise CloudError("Xiaomi prijava nije uspela")
        v = json.loads(body[len("&&&START&&&"):])
        if not v.get("location") or not v.get("ssecurity"):
            raise CloudError("Xiaomi prijava je istekla, prijavi se ponovo")
        self.ssecurity = base64.b64decode(v["ssecurity"])
        self.client.get(v["location"])  # sets serviceToken (and friends) in the cookie jar
        jar = {c.name: c.value for c in self.client.cookies.jar}
        if "serviceToken" not in jar:
            raise CloudError("Xiaomi nije vratio serviceToken")
        self.cookies = f"userId={jar.get('userId', user_id)}; cUserId={jar.get('cUserId', '')}; serviceToken={jar['serviceToken']}"

    def request(self, region: str, api: str, params: dict) -> object:
        nonce = os.urandom(8) + struct.pack(">I", int(time.time() // 60))
        signed_nonce = hashlib.sha256(self.ssecurity + nonce).digest()
        data = json.dumps(params, separators=(",", ":"))
        rc4_hash = _signature("POST", api, data, None, signed_nonce)
        enc = {k: base64.b64encode(_rc4(signed_nonce, v.encode())).decode()
               for k, v in (("data", data), ("rc4_hash__", rc4_hash))}
        form = {**enc, "signature": _signature("POST", api, enc["data"], enc["rc4_hash__"], signed_nonce),
                "_nonce": base64.b64encode(nonce).decode()}
        r = httpx.post(base_url(region) + api, data=form, headers={"Cookie": self.cookies}, timeout=15)
        if r.status_code != 200:
            raise CloudError(f"Xiaomi cloud: HTTP {r.status_code}")
        res = json.loads(_rc4(signed_nonce, base64.b64decode(r.text)))
        if res.get("code") != 0:
            raise CloudError(f"Xiaomi cloud: {res.get('message', res.get('code'))}")
        return res["result"]

    def close(self) -> None:
        self.client.close()


def device_tokens(user_id: str, region: str, dids: set[str]) -> dict[str, dict]:
    """{did: {name, model, localip, token}} for the given devices, read from the account's device list."""
    accounts = go2rtc_accounts()
    if user_id not in accounts:
        raise CloudError("go2rtc nema prijavu za ovaj Xiaomi nalog")
    cloud = Cloud(user_id, accounts[user_id])
    try:
        res = cloud.request(region, "/v2/home/device_list_page", {})
    finally:
        cloud.close()
    return {d["did"]: {k: d.get(k) for k in ("did", "name", "model", "localip", "token")}
            for d in res.get("list", []) if d.get("did") in dids}
