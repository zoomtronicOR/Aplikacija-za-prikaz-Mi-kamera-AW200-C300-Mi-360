"""Authenticated proxy to go2rtc: the browser never talks to go2rtc's API directly.

WebRTC signaling and MSE data go through /api/ws; WebRTC media itself flows over UDP/TCP 8555 to go2rtc.
"""
import asyncio

import httpx
import websockets
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import SESSION_COOKIE, current_user, user_from_token
from app.models import Camera
from app.services.go2rtc import auth_header

router = APIRouter()

PLAYER_FILES = ("video-stream.js", "video-rtc.js")
_player_cache: dict[str, bytes] = {}


# video-stream.js imports ./video-rtc.js relatively, so both live under /player/
@router.get("/player/{name}", include_in_schema=False, dependencies=[Depends(current_user)])
async def player_js(name: str):
    if name not in PLAYER_FILES:
        return Response(status_code=404)
    if name not in _player_cache:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{get_settings().go2rtc_url}/{name}", headers={"Authorization": auth_header()})
            r.raise_for_status()
            _player_cache[name] = r.content
    return Response(_player_cache[name], media_type="application/javascript")


def go2rtc_stream(cam: Camera, quality: str) -> str:
    return cam.sd_stream if quality == "sd" and cam.sd_stream else cam.slug


@router.websocket("/api/ws")
async def stream_ws(ws: WebSocket, src: str, quality: str = "hd"):
    with SessionLocal() as db:
        user = user_from_token(db, ws.cookies.get(SESSION_COOKIE))
        cam = db.execute(select(Camera).where(Camera.slug == src, Camera.enabled.is_(True))).scalar_one_or_none()
    if user is None or cam is None:
        await ws.close(code=4401 if user is None else 4404)
        return

    await ws.accept()
    upstream_url = get_settings().go2rtc_url.replace("http", "ws", 1) + f"/api/ws?src={go2rtc_stream(cam, quality)}"
    try:
        async with websockets.connect(upstream_url, additional_headers={"Authorization": auth_header()},
                                      max_size=None) as upstream:
            async def browser_to_go2rtc():
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    await upstream.send(msg["text"] if msg.get("text") is not None else msg["bytes"])

            async def go2rtc_to_browser():
                async for msg in upstream:
                    if isinstance(msg, bytes):
                        await ws.send_bytes(msg)
                    else:
                        await ws.send_text(msg)

            tasks = [asyncio.create_task(browser_to_go2rtc()), asyncio.create_task(go2rtc_to_browser())]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except (WebSocketDisconnect, websockets.ConnectionClosed, OSError):
        pass
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass
