"""Admin CLI, run inside the app container:

    docker compose exec app python -m app.cli create-user <username> <admin|user|viewer>
    docker compose exec app python -m app.cli import-cameras
"""
import argparse
import getpass
import json
import re
import sys

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import audit, hash_password
from app.models import ROLES, Camera, User

# matches go2rtc.yaml stream entries such as:
#   garden:
#     - xiaomi://1234567890:de@192.168.1.20?did=1000000001&model=isa.camera.hlc9a&subtype=3
STREAM_RE = re.compile(
    r"^  ([\w-]+):\n    - xiaomi://[^@\n]*?(?::(\w+))?@([\d.]+)\?did=(\d+)&model=([\w.]+)(?:&subtype=(\w+))?",
    re.M)


def create_user(username: str, role: str) -> None:
    password = getpass.getpass("Lozinka: ")
    if len(password) < 12:
        sys.exit("Lozinka mora imati najmanje 12 znakova.")
    if password != getpass.getpass("Ponovi lozinku: "):
        sys.exit("Lozinke se ne poklapaju.")
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user:
            user.password_hash, user.role, user.is_active = hash_password(password), role, True
            action = "user_updated"
        else:
            user = User(username=username, password_hash=hash_password(password), role=role)
            db.add(user)
            action = "user_created"
        db.commit()
        audit(db, action, username="cli", target=username, detail=f"role={role}")
    print(f"Korisnik '{username}' ({role}) je sačuvan.")


def import_cameras() -> None:
    s = get_settings()
    cfg = httpx.get(f"{s.go2rtc_url}/api/config", auth=(s.go2rtc_user, s.go2rtc_password)).text
    with open(s.miio_tokens_file) as f:
        names = {did: d["name"] for did, d in json.load(f).items()}

    streams = STREAM_RE.findall(cfg)
    # "<slug>_sd" streams are the SD profile of camera <slug>, not separate cameras
    sd_names = {slug for slug, *_ in streams if slug.endswith("_sd")}
    main = [s for s in streams if s[0] not in sd_names]

    with SessionLocal() as db:
        for i, (slug, region, ip, did, model, subtype) in enumerate(main):
            cam = db.execute(select(Camera).where(Camera.did == did)).scalar_one_or_none()
            if cam is None:
                cam = Camera(did=did, slug=slug, name=names.get(did, slug), sort_order=i)
                db.add(cam)
                print(f"+ {slug}: {cam.name} ({model}, {ip})")
            else:
                print(f"= {slug}: ažurirano ({model}, {ip})")
            cam.slug, cam.model, cam.ip, cam.region = slug, model, ip, region or "de"
            cam.stream_profile = subtype or None
            cam.sd_stream = f"{slug}_sd" if f"{slug}_sd" in sd_names else None
        db.commit()
        audit(db, "cameras_imported", username="cli")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user", help="napravi korisnika ili mu promeni lozinku i ulogu")
    cu.add_argument("username")
    cu.add_argument("role", choices=ROLES)
    sub.add_parser("import-cameras", help="uvezi kamere iz go2rtc konfiguracije")
    args = p.parse_args()

    if args.cmd == "create-user":
        create_user(args.username, args.role)
    else:
        import_cameras()


if __name__ == "__main__":
    main()
