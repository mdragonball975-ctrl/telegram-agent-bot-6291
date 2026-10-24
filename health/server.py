"""FastAPI health-check server (port 8080 / $PORT) running in the bot's event loop."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

HealthProbe = Callable[[], Awaitable[dict]]


def build_app(probe: HealthProbe) -> FastAPI:
    app = FastAPI(title="Telegram AI Agent", docs_url=None, redoc_url=None, openapi_url=None)
    started = time.monotonic()

    @app.get("/")
    async def root() -> dict:
        return {"service": "telegram-ai-agent", "status": "running"}

    @app.get("/health")
    async def health() -> JSONResponse:
        try:
            data = await asyncio.wait_for(probe(), timeout=10)
        except Exception as exc:  # health must never throw
            data = {"ok": False, "error": str(exc)}
        data["uptime_seconds"] = round(time.monotonic() - started, 1)
        data["time"] = datetime.now(timezone.utc).isoformat()
        return JSONResponse(data, status_code=200 if data.get("ok") else 503)

    return app


class HealthServer:
    def __init__(self, port: int, probe: HealthProbe) -> None:
        config = uvicorn.Config(
            build_app(probe), host="0.0.0.0", port=port,
            log_level="warning", access_log=False, lifespan="off",
        )
        self._server = uvicorn.Server(config)
        # We manage signals ourselves in main.py.
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._task: asyncio.Task | None = None
        self.port = port

    async def start(self) -> None:
        self._task = asyncio.create_task(self._server.serve(), name="health-server")
        log.info("Health server starting on 0.0.0.0:%d", self.port)

    async def stop(self) -> None:
        self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except Exception:
                self._task.cancel()
