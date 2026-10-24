"""Entry point: wires config, Mongo, AI client, workers, Telegram polling and health server."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

from bot.handlers import register_handlers
from bot.sender import log_to_channel
from config import get_settings
from core.ai_client import AIClient
from core.worker import WorkerPool
from db.mongo import Database
from health.server import HealthServer
from utils.logging_setup import setup_logging

log = logging.getLogger("main")


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    if not settings.admin_ids:
        log.warning("ADMIN_IDS is empty: nobody will be able to use the bot!")

    db = Database(settings.mongo_uri, settings.mongo_database)
    # Retry the initial connection so a slow Atlas cold start never crashes the deploy.
    for attempt in range(1, 11):
        try:
            await db.connect()
            break
        except Exception as exc:
            log.error("Mongo connect failed (attempt %d/10): %s", attempt, exc)
            if attempt == 10:
                raise
            await asyncio.sleep(min(30, 2 ** attempt))

    ai = AIClient(
        settings.ai_api_key, settings.ai_base_url, settings.ai_model,
        temperature=settings.ai_temperature, max_tokens=settings.ai_max_tokens,
        max_rpm=settings.ai_max_rpm, timeout=settings.request_timeout_seconds,
    )

    session = AiohttpSession(timeout=60)
    bot = Bot(settings.bot_token, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    register_handlers(dp, db, settings)

    workers = WorkerPool(bot, db, ai, settings)

    async def probe() -> dict:
        mongo_ok = await db.ping()
        workers_ok = workers.healthy()
        return {"ok": mongo_ok and workers_ok, "mongo": mongo_ok, "workers": workers_ok}

    health = HealthServer(settings.port, probe)
    await health.start()
    await workers.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # Windows
            pass

    me = await bot.get_me()
    log.info("Bot @%s started", me.username)
    await log_to_channel(bot, settings.log_channel, f"🟢 <b>@{me.username} online</b> — {settings.worker_count} workers")

    await bot.delete_webhook(drop_pending_updates=False)
    polling = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False, allowed_updates=dp.resolve_used_update_types()),
        name="polling",
    )
    stopper = asyncio.create_task(stop_event.wait())
    done, _ = await asyncio.wait({polling, stopper}, return_when=asyncio.FIRST_COMPLETED)
    if polling in done and polling.exception():
        log.error("Polling crashed: %r", polling.exception())

    log.info("Shutting down…")
    try:
        await dp.stop_polling()
    except Exception:
        pass
    polling.cancel()
    stopper.cancel()
    await asyncio.gather(polling, stopper, return_exceptions=True)
    await log_to_channel(bot, settings.log_channel, f"🔴 <b>@{me.username} stopping</b>")
    await workers.stop()
    await health.stop()
    await ai.close()
    await bot.session.close()
    await db.close()
    log.info("Shutdown complete")
    # Non-zero exit if polling crashed so Railway restarts us.
    if polling in done and not polling.cancelled() and polling.exception():
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
