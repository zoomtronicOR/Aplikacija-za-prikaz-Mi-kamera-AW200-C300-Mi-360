"""Encryption for secrets that must live in the DB (e.g. the SMB share password).

The key is APP_SECRET_KEY from .env (a Fernet key: `python -c "from cryptography.fernet import Fernet;
print(Fernet.generate_key().decode())"`), so a DB dump alone doesn't reveal the secrets.
"""
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().app_secret_key.encode())


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("lozinka ne može da se dešifruje (promenjen APP_SECRET_KEY?)") from e
