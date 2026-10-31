import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import cameras, events, notify, pages, recordings, stream, system, users, xiaomi
from app.notify.notifier import mqtt, notifier_loop
from app.services.monitor import monitor_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(monitor_loop()), asyncio.create_task(notifier_loop())]
    yield
    for t in tasks:
        t.cancel()
    mqtt.stop()


app = FastAPI(title="Xiaomi Camera Hub", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(pages.router)
app.include_router(cameras.router)
app.include_router(recordings.router)
app.include_router(xiaomi.router)
app.include_router(notify.router)
app.include_router(events.router)
app.include_router(users.router)
app.include_router(system.router)
app.include_router(stream.router)
