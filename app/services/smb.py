"""SMB (Windows share / NAS) access for recordings, in userspace.

The LXC is unprivileged and can't mount CIFS, so files are copied with smbprotocol's client instead.
All functions are blocking; call them through asyncio.to_thread from async code.
"""
import posixpath
import shutil
import uuid
from dataclasses import dataclass

import smbclient
from smbprotocol.exceptions import SMBException
from sqlalchemy.orm import Session

from app.core.crypto import decrypt
from app.models import RecordingStorage

CHUNK = 1024 * 1024


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class Share:
    host: str
    share: str
    folder: str
    username: str
    password: str

    def unc(self, rel: str = "") -> str:
        parts = [p for p in posixpath.join(self.folder.strip("/\\"), rel).replace("\\", "/").split("/") if p]
        if any(p == ".." for p in parts):
            raise StorageError("nedozvoljena putanja")
        return "\\\\" + "\\".join([self.host, self.share, *parts])

    def _session(self) -> None:
        try:
            smbclient.register_session(self.host, username=self.username, password=self.password,
                                       connection_timeout=10)
        except (SMBException, OSError, ValueError) as e:
            raise StorageError(_explain(e)) from e


def _explain(e: Exception) -> str:
    text = str(e)
    if "STATUS_LOGON_FAILURE" in text:
        return "pogrešno korisničko ime ili lozinka"
    if "authenticated as a guest" in text:
        # Windows with the Guest account enabled falls back to guest instead of rejecting the login
        return ("Windows nije prihvatio korisnika i lozinku (prijava kao gost). Za Microsoft nalog unesi "
                "njegov email i lozinku (ne PIN), ili napravi lokalnog korisnika za snimke")
    if "STATUS_BAD_NETWORK_NAME" in text or "STATUS_NOT_FOUND" in text:
        return "deljeni folder ne postoji (proveri ime deljenja)"
    if "STATUS_ACCESS_DENIED" in text:
        return "nema dozvole za upis u folder"
    if isinstance(e, (TimeoutError, ConnectionRefusedError)) or "timed out" in text or "refused" in text.lower():
        return "računar ne odgovara (uključen? deljenje fajlova dozvoljeno u firewall-u?)"
    return text[:300]


def load(db: Session) -> Share | None:
    st = db.get(RecordingStorage, 1)
    if not st or not (st.host and st.share and st.username and st.password_enc):
        return None
    return Share(st.host, st.share, st.folder or "", st.username, decrypt(st.password_enc))


def reset() -> None:
    """Drops cached connections, e.g. after the credentials changed."""
    smbclient.reset_connection_cache()


def check(share: Share) -> None:
    """Writes, reads back and deletes a small file. Raises StorageError with a readable message."""
    share._session()
    name = f".kamere-test-{uuid.uuid4().hex[:8]}"
    try:
        smbclient.makedirs(share.unc(), exist_ok=True)
        with smbclient.open_file(share.unc(name), mode="wb") as f:
            f.write(b"ok")
        with smbclient.open_file(share.unc(name), mode="rb") as f:
            if f.read() != b"ok":
                raise StorageError("upisani fajl se ne čita ispravno")
        smbclient.remove(share.unc(name))
    except (SMBException, OSError) as e:
        raise StorageError(_explain(e)) from e


def capacity(share: Share) -> tuple[int, int]:
    """(total, free) bytes of the disk behind the share, as the share's user sees it."""
    share._session()
    try:
        v = smbclient.stat_volume(share.unc())
    except (SMBException, OSError) as e:
        raise StorageError(_explain(e)) from e
    return v.total_size, v.caller_available_size


def upload(share: Share, local_path: str, rel: str) -> None:
    share._session()
    try:
        smbclient.makedirs(share.unc(posixpath.dirname(rel)), exist_ok=True)
        tmp = rel + ".part"
        with open(local_path, "rb") as src, smbclient.open_file(share.unc(tmp), mode="wb") as dst:
            shutil.copyfileobj(src, dst, CHUNK)
        # rename after a complete copy, so a half-written file never looks like a recording
        smbclient.rename(share.unc(tmp), share.unc(rel))
    except (SMBException, OSError) as e:
        raise StorageError(_explain(e)) from e


def delete(share: Share, rel: str) -> None:
    share._session()
    try:
        smbclient.remove(share.unc(rel))
    except (SMBException, OSError) as e:
        if _missing(e):
            return  # already gone (e.g. deleted by hand on the share)
        raise StorageError(_explain(e)) from e


# STATUS_OBJECT_NAME_NOT_FOUND, STATUS_OBJECT_PATH_NOT_FOUND
_MISSING = ("0xc0000034", "0xc000003a", "OBJECT_NAME_NOT_FOUND", "OBJECT_PATH_NOT_FOUND")


def _missing(e: Exception) -> bool:
    """smbclient raises SMBOSError (errno 2) whose text carries the NTSTATUS hex, not FileNotFoundError."""
    return isinstance(e, FileNotFoundError) or getattr(e, "errno", None) == 2 or any(m in str(e) for m in _MISSING)


def existing(share: Share, dirs: set[str]) -> set[str] | None:
    """Relative paths of the files present in the given share directories; None when a listing fails
    for another reason than a missing directory (then nothing may be treated as missing)."""
    share._session()
    found: set[str] = set()
    for d in dirs:
        try:
            for entry in smbclient.scandir(share.unc(d)):
                found.add(posixpath.join(d, entry.name))
        except (SMBException, OSError) as e:
            if not _missing(e):
                return None
    return found


def open_read(share: Share, rel: str):
    share._session()
    try:
        return smbclient.open_file(share.unc(rel), mode="rb")
    except (SMBException, OSError) as e:
        raise StorageError(_explain(e)) from e
