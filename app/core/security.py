import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, WebSocket
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models import AuditLog, User, UserSession

SESSION_COOKIE = "camhub_session"
_hasher = PasswordHasher()  # Argon2id


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request | WebSocket) -> str:
    return request.client.host if request.client else ""


def create_session(db: Session, user: User, ip: str) -> str:
    token = secrets.token_urlsafe(32)
    ttl = timedelta(hours=get_settings().session_ttl_hours)
    db.add(UserSession(token_hash=_token_hash(token), user_id=user.id, ip=ip,
                       expires_at=datetime.now(timezone.utc) + ttl))
    db.execute(delete(UserSession).where(UserSession.expires_at < datetime.now(timezone.utc)))
    db.commit()
    return token


def delete_session(db: Session, token: str) -> None:
    db.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(token)))
    db.commit()


def delete_user_sessions(db: Session, user_id: int, keep_token: str | None = None) -> None:
    """Logs a user out everywhere (optionally except the current session)."""
    q = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_token:
        q = q.where(UserSession.token_hash != _token_hash(keep_token))
    db.execute(q)
    db.commit()


MIN_PASSWORD_LEN = 12


def user_from_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    row = db.execute(
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(UserSession.token_hash == _token_hash(token),
               UserSession.expires_at > datetime.now(timezone.utc),
               User.is_active.is_(True))
    ).scalar_one_or_none()
    return row


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    return user_from_token(db, request.cookies.get(SESSION_COOKIE))


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(401, "Niste prijavljeni")
    return user


def require_role(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Nemate dozvolu za ovu radnju")
        return user
    return dep


def audit(db: Session, action: str, *, user: User | None = None, username: str | None = None,
          target: str | None = None, detail: str | None = None, ip: str | None = None) -> None:
    db.add(AuditLog(action=action, user_id=user.id if user else None,
                    username=user.username if user else username,
                    target=target, detail=detail, ip=ip))
    db.commit()


class LoginThrottle:
    """In-memory brute-force guard: too many failures per IP or username blocks further attempts."""

    def __init__(self, max_failures: int = 5, window_s: int = 900):
        self.max_failures, self.window_s = max_failures, window_s
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> deque[float]:
        q, cutoff = self._fails[key], time.monotonic() - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, ip: str, username: str) -> bool:
        return any(len(self._recent(k)) >= self.max_failures for k in (f"ip:{ip}", f"u:{username.lower()}"))

    def fail(self, ip: str, username: str) -> None:
        for k in (f"ip:{ip}", f"u:{username.lower()}"):
            self._recent(k).append(time.monotonic())

    def reset(self, ip: str, username: str) -> None:
        for k in (f"ip:{ip}", f"u:{username.lower()}"):
            self._fails.pop(k, None)


login_throttle = LoginThrottle()
