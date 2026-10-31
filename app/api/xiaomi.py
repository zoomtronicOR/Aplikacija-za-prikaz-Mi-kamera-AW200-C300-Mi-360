"""Adding Xiaomi cameras from the UI: Xiaomi login (through go2rtc), device list, one-click add.

Adding a camera:
1. Finds working HD and SD stream profiles. Known models use their verified `subtype`; others are
   probed in order.
2. Writes both streams into go2rtc. An on-demand Opus audio source is included and only runs for
   cameras with PCMA audio.
3. Fetches the device token from the Xiaomi cloud, using go2rtc's login, into the tokens file.
4. For models the app doesn't know, derives controls from the MIoT spec and keeps those the camera
   answers.
5. Saves the camera.
"""
import asyncio
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.cameras import camera_json, slugify
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import audit, client_ip, require_role
from app.models import Camera, User
from app.services import go2rtc, miio, miot_spec, xiaomi_cloud
from app.services.controls import MODELS, model_from_json, save_token

router = APIRouter(prefix="/api/xiaomi", dependencies=[Depends(require_role("admin"))])

REGIONS = {"de": "Evropa", "cn": "Kina", "us": "SAD", "sg": "Singapur", "ru": "Rusija", "i2": "Indija"}
# verified stream profiles (see the camera facts); unknown models are probed in this order
HD_PROFILES = {"isa.camera.hlc9a": ["3"], "xiaomi.camera.c01a01": ["3"], "chuangmi.camera.ipc019": [None]}
HD_PROBE_ORDER = [None, "3", "0", "hd"]
SD_PROBE_ORDER = ["1", "sd"]
PTZ_DEFAULT = {"xiaomi.camera.c01a01": True, "chuangmi.camera.ipc019": True, "isa.camera.hlc9a": False}


def _client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(base_url=s.go2rtc_url, auth=(s.go2rtc_user, s.go2rtc_password), timeout=60)


# --- login -------------------------------------------------------------------------------------

@router.get("/accounts")
async def accounts():
    async with _client() as c:
        r = await c.get("/api/xiaomi")
    r.raise_for_status()
    return {"accounts": r.json() or [], "regions": REGIONS}


class LoginIn(BaseModel):
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)
    captcha: str | None = Field(default=None, max_length=32)
    verify: str | None = Field(default=None, max_length=32)


@router.post("/login")
async def login(body: LoginIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    """One step of go2rtc's Xiaomi login: credentials, then captcha and/or a verification code."""
    form = {k: v for k, v in body.model_dump().items() if v}
    if not form:
        raise HTTPException(400, "Unesi podatke za prijavu")
    async with _client() as c:
        r = await c.post("/api/xiaomi", data=form)
    if r.status_code == 401:  # go2rtc wants a captcha or a code sent by email/SMS
        d = r.json()
        if d.get("captcha"):
            return {"status": "captcha", "image": d["captcha"]}
        return {"status": "verify", "target": d.get("verify_email") or d.get("verify_phone") or ""}
    if r.status_code != 200:
        audit(db, "xiaomi_login_failed", user=user, ip=client_ip(request))
        raise HTTPException(400, f"Prijava nije uspela: {r.text.strip()[:200]}")
    audit(db, "xiaomi_login", user=user, ip=client_ip(request))  # never the password
    return {"status": "ok"}


# --- devices -----------------------------------------------------------------------------------

def _parse_source(url: str) -> dict:
    parts = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    return {"ip": parts.hostname, "did": q.get("did"), "model": q.get("model")}


async def _devices(account: str, region: str) -> list[dict]:
    async with _client() as c:
        r = await c.get("/api/xiaomi", params={"id": account, "region": region})
    if r.status_code != 200:
        raise HTTPException(502, f"Lista uređaja nije stigla: {r.text.strip()[:200]}")
    out = []
    for s in r.json().get("sources", []):
        mac = re.search(r"mac: ([0-9A-Fa-f:]+)", s.get("info", ""))
        out.append({"name": s["name"], "url": s["url"], "mac": mac[1] if mac else None, **_parse_source(s["url"])})
    return out


@router.get("/devices")
async def devices(account: str, region: str = "de", db: Session = Depends(get_db)):
    found = await _devices(account, region)
    added = dict(db.execute(select(Camera.did, Camera.name).where(Camera.did.is_not(None))).all())
    reach = await asyncio.gather(*(asyncio.to_thread(miio.ping, d["ip"], 1.5) for d in found))
    return [{k: v for k, v in d.items() if k != "url"}
            | {"added_as": added.get(d["did"]), "reachable": ok, "known_model": d["model"] in MODELS}
            for d, ok in zip(found, reach)]


# --- add ---------------------------------------------------------------------------------------

class AddIn(BaseModel):
    account: str = Field(min_length=1, max_length=32)
    region: str = Field(default="de", pattern=r"^(de|cn|us|sg|ru|i2)$")
    did: str = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    ptz: bool | None = None


def _with_subtype(url: str, subtype: str | None) -> str:
    url = re.sub(r"&subtype=\w+", "", url)
    return url + (f"&subtype={subtype}" if subtype else "")


async def _first_working(url: str, profiles: list[str | None]) -> tuple[str | None, str] | None:
    for p in profiles:
        try:
            return p, await go2rtc.probe(_with_subtype(url, p))
        except go2rtc.SourceError:
            continue
    return None


async def _verified_controls(model: str, ip: str, did: str, token: str) -> tuple[str | None, int]:
    """Spec-derived controls kept only where the camera returns a value."""
    spec = await asyncio.to_thread(miot_spec.fetch_spec, model)
    if not spec:
        return None, 0
    derived = miot_spec.controls_from_spec(spec)
    if not derived["props"]:
        return None, 0
    dev = miio.MiioDevice(ip, token)
    keys = list(derived["props"])
    try:
        res = await dev.send("get_properties", [{"did": did, "siid": derived["props"][k][0], "piid": derived["props"][k][1]}
                                                for k in keys])
    except miio.MiioError:
        return None, 0  # e.g. a legacy-only camera: no controls rather than broken ones
    derived["props"] = {k: derived["props"][k] for k, r in zip(keys, res) if "value" in r}
    if not derived["props"]:
        return None, 0
    data = json.dumps(derived)
    model_from_json(data)  # fail here, not at runtime, if something doesn't map
    return data, len(derived["props"])


@router.post("/cameras", status_code=201)
async def add_camera(body: AddIn, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin"))):
    if db.execute(select(Camera).where(Camera.did == body.did)).scalar_one_or_none():
        raise HTTPException(409, "Ova kamera je već dodata")
    dev = next((d for d in await _devices(body.account, body.region) if d["did"] == body.did), None)
    if dev is None:
        raise HTTPException(404, "Kamera nije na ovom nalogu/regionu")
    model, report = dev["model"], {}

    hd = await _first_working(dev["url"], HD_PROFILES.get(model, HD_PROBE_ORDER))
    if hd is None:
        raise HTTPException(422, "Kamera ne daje sliku (proveri da je uključena, u istoj mreži i da je ne gleda Mi Home)")
    sd = await _first_working(dev["url"], SD_PROBE_ORDER)
    report["hd"] = hd[1]
    report["sd"] = sd[1] if sd else None

    try:
        tokens = await asyncio.to_thread(xiaomi_cloud.device_tokens, body.account, body.region, {body.did})
    except xiaomi_cloud.CloudError as e:
        raise HTTPException(502, f"Token nije preuzet: {e}")
    entry = tokens.get(body.did)
    if not entry or not entry.get("token"):
        raise HTTPException(502, "Xiaomi nalog nije vratio token za ovu kameru")

    controls_json, report["controls"] = None, len(MODELS[model].controls) if model in MODELS else 0
    if model not in MODELS:
        controls_json, report["controls"] = await _verified_controls(model, dev["ip"], body.did, entry["token"])

    name = (body.name or dev["name"]).strip()
    taken = set(db.execute(select(Camera.slug)).scalars()) | set(db.execute(select(Camera.sd_stream)).scalars())
    slug = slugify(name, taken)
    await go2rtc.put_stream(slug, [_with_subtype(dev["url"], hd[0]), f"ffmpeg:{slug}#audio=opus"])
    if sd:
        await go2rtc.put_stream(f"{slug}_sd", [_with_subtype(dev["url"], sd[0]), f"ffmpeg:{slug}_sd#audio=opus"])
    await asyncio.to_thread(save_token, body.did, entry)

    cam = Camera(slug=slug, name=name, location=(body.location or "").strip() or None, kind="xiaomi",
                 model=model, did=body.did, ip=dev["ip"], mac=dev["mac"], region=body.region,
                 stream_profile=hd[0], sd_stream=f"{slug}_sd" if sd else None, controls_json=controls_json,
                 ptz=body.ptz if body.ptz is not None else PTZ_DEFAULT.get(model),
                 sort_order=(db.execute(select(func.max(Camera.sort_order))).scalar() or 0) + 1)
    db.add(cam)
    db.commit()
    audit(db, "xiaomi_camera_added", user=user, target=slug, detail=f"{model} {dev['ip']} {report}",
          ip=client_ip(request))
    return {**camera_json(cam, user), "report": report}
