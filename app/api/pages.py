from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User | None = Depends(optional_user)):
    if user is None:
        return RedirectResponse("/login", 303)
    return templates.TemplateResponse(request, "dashboard.html", {"user": user})


@router.get("/admin/cameras", response_class=HTMLResponse)
def admin_cameras(request: Request, user: User | None = Depends(optional_user)):
    if user is None:
        return RedirectResponse("/login", 303)
    if user.role != "admin":
        return RedirectResponse("/", 303)
    return templates.TemplateResponse(request, "admin_cameras.html", {"user": user})


@router.get("/admin/recording", response_class=HTMLResponse)
def admin_recording(request: Request, user: User | None = Depends(optional_user)):
    if user is None:
        return RedirectResponse("/login", 303)
    if user.role != "admin":
        return RedirectResponse("/", 303)
    return templates.TemplateResponse(request, "admin_recording.html", {"user": user})


@router.get("/admin/notify", response_class=HTMLResponse)
def admin_notify(request: Request, user: User | None = Depends(optional_user)):
    if user is None:
        return RedirectResponse("/login", 303)
    if user.role != "admin":
        return RedirectResponse("/", 303)
    return templates.TemplateResponse(request, "admin_notify.html", {"user": user})


@router.get("/recordings", response_class=HTMLResponse)
def recordings_page(request: Request, user: User | None = Depends(optional_user)):
    if user is None:
        return RedirectResponse("/login", 303)
    return templates.TemplateResponse(request, "recordings.html", {"user": user})


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

    resp = RedirectResponse("/", 303)
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
