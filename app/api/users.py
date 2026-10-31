"""User administration (admin), audit log viewer (admin), and the user's own password."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import (MIN_PASSWORD_LEN, SESSION_COOKIE, audit, client_ip, current_user,
                               delete_user_sessions, hash_password, require_role, verify_password)
from app.models import ROLES, AuditLog, User

router = APIRouter()
admin = require_role("admin")

USERNAME = r"^[A-Za-z0-9._-]{3,64}$"
ROLE_LABELS = {"admin": "administrator", "user": "korisnik", "viewer": "pregled"}


def _user_json(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active,
            "must_change_password": u.must_change_password, "created_at": u.created_at, "last_login_at": u.last_login_at}


def _check_password(pw: str) -> None:
    if len(pw) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Lozinka mora imati najmanje {MIN_PASSWORD_LEN} znakova")


def _active_admins(db: Session, excluding: int | None = None) -> int:
    q = select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
    if excluding is not None:
        q = q.where(User.id != excluding)
    return db.execute(q).scalar()


def _get(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "Korisnik ne postoji")
    return u


# --- admin: users -------------------------------------------------------------------------------

@router.get("/api/users")
def list_users(db: Session = Depends(get_db), me: User = Depends(admin)):
    return {"me": me.id, "roles": ROLE_LABELS,
            "users": [_user_json(u) for u in db.execute(select(User).order_by(User.username)).scalars()]}


class NewUser(BaseModel):
    username: str = Field(pattern=USERNAME)
    role: str
    password: str = Field(max_length=256)
    must_change_password: bool = True


@router.post("/api/users", status_code=201)
def create_user(body: NewUser, request: Request, db: Session = Depends(get_db), me: User = Depends(admin)):
    if body.role not in ROLES:
        raise HTTPException(400, "Nepoznata uloga")
    _check_password(body.password)
    if db.execute(select(User).where(func.lower(User.username) == body.username.lower())).scalar_one_or_none():
        raise HTTPException(409, "Korisnik sa tim imenom već postoji")
    u = User(username=body.username, role=body.role, password_hash=hash_password(body.password),
             must_change_password=body.must_change_password)
    db.add(u)
    db.commit()
    audit(db, "user_created", user=me, target=u.username, detail=f"role={u.role}", ip=client_ip(request))
    return _user_json(u)


class UpdateUser(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    must_change_password: bool | None = None


@router.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UpdateUser, request: Request, db: Session = Depends(get_db),
                me: User = Depends(admin)):
    u = _get(db, user_id)
    if body.role is not None and body.role not in ROLES:
        raise HTTPException(400, "Nepoznata uloga")
    losing_admin = u.role == "admin" and u.is_active and (
        (body.role is not None and body.role != "admin") or body.is_active is False)
    if u.id == me.id and losing_admin:
        raise HTTPException(400, "Ne možeš sebi da oduzmeš administratorska prava ili da isključiš svoj nalog")
    if losing_admin and _active_admins(db, excluding=u.id) == 0:
        raise HTTPException(400, "Mora ostati bar jedan aktivan administrator")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for k, v in changes.items():
        setattr(u, k, v)
    db.commit()
    if body.is_active is False or "role" in changes:
        delete_user_sessions(db, u.id)  # takes effect immediately, not at the next login
    audit(db, "user_updated", user=me, target=u.username, detail=str(changes), ip=client_ip(request))
    return _user_json(u)


class SetPassword(BaseModel):
    password: str = Field(max_length=256)
    must_change_password: bool = True


@router.post("/api/users/{user_id}/password")
def set_password(user_id: int, body: SetPassword, request: Request, db: Session = Depends(get_db),
                 me: User = Depends(admin)):
    u = _get(db, user_id)
    _check_password(body.password)
    u.password_hash = hash_password(body.password)
    u.must_change_password = body.must_change_password and u.id != me.id
    db.commit()
    delete_user_sessions(db, u.id, keep_token=request.cookies.get(SESSION_COOKIE) if u.id == me.id else None)
    audit(db, "user_password_set", user=me, target=u.username, ip=client_ip(request))
    return _user_json(u)


@router.delete("/api/users/{user_id}", status_code=204)
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db), me: User = Depends(admin)):
    u = _get(db, user_id)
    if u.id == me.id:
        raise HTTPException(400, "Ne možeš da obrišeš svoj nalog")
    if u.role == "admin" and u.is_active and _active_admins(db, excluding=u.id) == 0:
        raise HTTPException(400, "Mora ostati bar jedan aktivan administrator")
    name = u.username
    db.delete(u)  # sessions go with it (ON DELETE CASCADE); audit rows keep the username
    db.commit()
    audit(db, "user_deleted", user=me, target=name, ip=client_ip(request))


# --- admin: audit log ---------------------------------------------------------------------------

@router.get("/api/audit")
def audit_log(username: str | None = None, action: str | None = None, offset: int = 0, limit: int = 100,
              db: Session = Depends(get_db), me: User = Depends(admin)):
    q = select(AuditLog)
    if username:
        q = q.where(AuditLog.username == username)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(AuditLog.id.desc()).offset(max(0, offset)).limit(min(limit, 500))).scalars()
    actions = sorted({a.split("_")[0] for a in db.execute(select(AuditLog.action).distinct()).scalars()})
    return {"total": total, "actions": actions,
            "items": [{"ts": a.ts, "username": a.username, "action": a.action, "target": a.target,
                       "detail": a.detail, "ip": a.ip} for a in rows]}


# --- own account --------------------------------------------------------------------------------

class OwnPassword(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


@router.post("/api/me/password")
def change_own_password(body: OwnPassword, request: Request, db: Session = Depends(get_db),
                        me: User = Depends(current_user)):
    if not verify_password(me.password_hash, body.current):
        audit(db, "own_password_failed", user=me, ip=client_ip(request))
        raise HTTPException(400, "Trenutna lozinka nije tačna")
    _check_password(body.new)
    if body.new == body.current:
        raise HTTPException(400, "Nova lozinka mora biti drugačija od trenutne")
    me.password_hash, me.must_change_password = hash_password(body.new), False
    db.commit()
    delete_user_sessions(db, me.id, keep_token=request.cookies.get(SESSION_COOKIE))  # other devices log out
    audit(db, "own_password_changed", user=me, ip=client_ip(request))
    return {"ok": True}


@router.get("/api/me")
def whoami(me: User = Depends(current_user)):
    return _user_json(me) | {"role_label": ROLE_LABELS.get(me.role, me.role)}
