from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import (SESSION_COOKIE, audit, client_ip, create_session, delete_session,
                               login_throttle, optional_user, verify_password)
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
STATIC = Path(__file__).parent.parent / "static"


def _redirect(to: str) -> HTTPException:
    return HTTPException(303, headers={"Location": to})


def page_user(request: Request, user: User | None = Depends(optional_user)) -> User:
    """Logged-in user for an HTML page. A user who must set a new password goes to /account first."""
    if user is None:
        raise _redirect("/login")
    if user.must_change_password and request.url.path != "/account":
        raise _redirect("/account")
    return user


def page_admin(user: User = Depends(page_user)) -> User:
    if user.role != "admin":
        raise _redirect("/")
    return user


def _page(template: str, admin: bool = False):
    dep = page_admin if admin else page_user

    def handler(request: Request, user: User = Depends(dep)):
        return templates.TemplateResponse(request, template, {"user": user})
    return handler


for path, template, is_admin in (
    ("/", "dashboard.html", False),
    ("/events", "events.html", False),
    ("/recordings", "recordings.html", False),
    ("/account", "account.html", False),
    ("/admin/cameras", "admin_cameras.html", True),
    ("/admin/recording", "admin_recording.html", True),
    ("/admin/notify", "admin_notify.html", True),
    ("/admin/users", "admin_users.html", True),
):
    router.add_api_route(path, _page(template, is_admin), methods=["GET"], response_class=HTMLResponse,
                         include_in_schema=False)


@router.get("/camera/{slug}", response_class=HTMLResponse)
def camera_page(slug: str, request: Request, user: User = Depends(page_user)):
    return templates.TemplateResponse(request, "camera.html", {"user": user, "slug": slug})


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    # browsers ask for /favicon.ico on their own, before any page links it
    return FileResponse(STATIC / "favicon.ico", media_type="image/x-icon", headers={"Cache-Control": "public, max-age=604800"})


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    ip = client_ip(request)
    if login_throttle.blocked(ip, username):
        audit(db, "login_blocked", username=username, ip=ip)
        return templates.TemplateResponse(
            request, "login.html", {"error": "Previše neuspešnih pokušaja. Pokušajte ponovo za 15 minuta."}, 429)

    user = db.execute(select(User).where(User.username == username, User.is_active.is_(True))).scalar_one_or_none()
    if user is None or not verify_password(user.password_hash, password):
        login_throttle.fail(ip, username)
        audit(db, "login_failed", username=username, ip=ip)
        return templates.TemplateResponse(request, "login.html", {"error": "Pogrešno korisničko ime ili lozinka."}, 401)

    login_throttle.reset(ip, username)
    user.last_login_at = datetime.now(timezone.utc)
    token = create_session(db, user, ip)
    audit(db, "login", user=user, ip=ip)

    resp = RedirectResponse("/account" if user.must_change_password else "/", 303)
    settings = get_settings()
    resp.set_cookie(SESSION_COOKIE, token, max_age=settings.session_ttl_hours * 3600,
                    httponly=True, secure=settings.cookie_secure, samesite="strict")
    return resp


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db), user: User | None = Depends(optional_user)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(db, token)
    if user:
        audit(db, "logout", user=user, ip=client_ip(request))
    resp = RedirectResponse("/login", 303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp
