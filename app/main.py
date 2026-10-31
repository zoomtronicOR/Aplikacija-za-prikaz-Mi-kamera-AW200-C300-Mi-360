import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import cameras, pages, recordings, stream
from app.services.monitor import monitor_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(monitor_loop())
    yield
    task.cancel()


app = FastAPI(title="Xiaomi Camera Hub", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(pages.router)
app.include_router(cameras.router)
app.include_router(recordings.router)
app.include_router(stream.router)
