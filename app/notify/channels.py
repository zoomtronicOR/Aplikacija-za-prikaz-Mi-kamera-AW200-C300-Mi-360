"""Push channels: Telegram, ntfy, email. Blocking; call through asyncio.to_thread.

Each sender gets the channel's JSON config (no secrets) and its decrypted secret, and raises
ChannelError with a readable message on failure.
"""
import base64
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import httpx


class ChannelError(Exception):
    pass


@dataclass
class Message:
    title: str
    text: str
    image: bytes | None = None
    tags: str = ""          # ntfy emoji tags
    priority: str = "default"


def _check(r: httpx.Response, what: str) -> None:
    if r.status_code >= 300:
        raise ChannelError(f"{what}: HTTP {r.status_code} {r.text.strip()[:200]}")


def send_telegram(cfg: dict, secret: str | None, msg: Message) -> None:
    if not secret or not cfg.get("chat_id"):
        raise ChannelError("unesi token bota i chat ID")
    base = f"https://api.telegram.org/bot{secret}"
    text = f"<b>{_html(msg.title)}</b>\n{_html(msg.text)}"
    try:
        if msg.image:
            r = httpx.post(f"{base}/sendPhoto", data={"chat_id": cfg["chat_id"], "caption": text, "parse_mode": "HTML"},
                           files={"photo": ("kamera.jpg", msg.image, "image/jpeg")}, timeout=30)
        else:
            r = httpx.post(f"{base}/sendMessage", data={"chat_id": cfg["chat_id"], "text": text, "parse_mode": "HTML"},
                           timeout=15)
    except httpx.HTTPError as e:
        raise ChannelError(f"Telegram nije dostupan ({e.__class__.__name__})") from e
    if r.status_code == 401:
        raise ChannelError("Telegram: pogrešan token bota")
    if r.status_code == 400 and "chat not found" in r.text:
        raise ChannelError("Telegram: chat nije pronađen (pošalji botu /start, pa proveri chat ID)")
    _check(r, "Telegram")


def send_ntfy(cfg: dict, secret: str | None, msg: Message) -> None:
    server, topic = (cfg.get("server") or "https://ntfy.sh").rstrip("/"), cfg.get("topic")
    if not topic:
        raise ChannelError("unesi ntfy temu (topic)")
    headers = {"Title": _ascii_header(msg.title), "Priority": msg.priority}
    if msg.tags:
        headers["Tags"] = msg.tags
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    try:
        if msg.image:
            # an attachment goes as the body; the text then travels in the Message header
            headers |= {"Filename": "kamera.jpg", "Message": _ascii_header(msg.text)}
            r = httpx.put(f"{server}/{topic}", content=msg.image, headers=headers, timeout=30)
        else:
            r = httpx.post(f"{server}/{topic}", content=msg.text.encode(), headers=headers, timeout=15)
    except httpx.HTTPError as e:
        raise ChannelError(f"ntfy server nije dostupan ({e.__class__.__name__})") from e
    _check(r, "ntfy")


def send_email(cfg: dict, secret: str | None, msg: Message) -> None:
    host, port = cfg.get("host"), int(cfg.get("port") or 587)
    recipients = [a.strip() for a in (cfg.get("recipients") or "").split(",") if a.strip()]
    sender = cfg.get("sender") or cfg.get("username")
    if not host or not recipients or not sender:
        raise ChannelError("unesi SMTP server, pošiljaoca i primaoce")
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = msg.title, sender, ", ".join(recipients)
    m.set_content(msg.text)
    if msg.image:
        m.add_attachment(msg.image, maintype="image", subtype="jpeg", filename="kamera.jpg")
    security = cfg.get("security") or "starttls"
    try:
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
            if security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
        with smtp:
            if cfg.get("username"):
                smtp.login(cfg["username"], secret or "")
            smtp.send_message(m)
    except smtplib.SMTPAuthenticationError as e:
        raise ChannelError("email: pogrešno korisničko ime ili lozinka (za Gmail koristi lozinku za aplikacije)") from e
    except (smtplib.SMTPException, OSError) as e:
        raise ChannelError(f"email: {e}") from e


def _html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ascii_header(s: str) -> str:
    # HTTP headers are latin-1; ntfy decodes RFC 2047, so non-ASCII text is sent that way
    try:
        s.encode("latin-1")
        return s
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(s.encode()).decode() + "?="


SENDERS = {"telegram": send_telegram, "ntfy": send_ntfy, "email": send_email}
