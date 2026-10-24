"""Telegram command & message handlers. Admin-gated. Enqueues tasks for workers."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.sender import safe_send
from config import Settings
from db.mongo import Database
from utils.text import escape, truncate

log = logging.getLogger(__name__)


def register_handlers(dp: Dispatcher, db: Database, settings: Settings) -> None:

    async def _guard(message: Message) -> bool:
        uid = message.from_user.id if message.from_user else None
        if not settings.is_admin(uid):
            await safe_send(message.bot, message.chat.id,
                            "⛔ This is a private agent. You are not authorised to use it.")
            log.info("Rejected non-admin user %s", uid)
            return False
        return True

    @dp.message(CommandStart())
    async def on_start(message: Message) -> None:
        if not await _guard(message):
            return
        await safe_send(message.bot, message.chat.id,
            "🤖 <b>Private AI Agent online.</b>\n\n"
            "Send me any task and I'll reason through it autonomously, using a sandboxed "
            "workspace, Python execution, and web fetches as needed.\n\n"
            "<b>Commands</b>\n"
            "/help – show help\n"
            "/status – queue & service status\n"
            "/reset – clear this chat's memory\n"
            "/cancel – cancel my queued/running tasks")

    @dp.message(Command("help"))
    async def on_help(message: Message) -> None:
        if not await _guard(message):
            return
        await safe_send(message.bot, message.chat.id,
            "Just type what you want done. Examples:\n"
            "• <i>Write a Python script that computes primes and run it.</i>\n"
            "• <i>Fetch example.com and summarise it.</i>\n"
            "• <i>Create report.md with a project plan.</i>\n\n"
            "I keep short-term memory per chat (clear with /reset).")

    @dp.message(Command("status"))
    async def on_status(message: Message) -> None:
        if not await _guard(message):
            return
        stats = await db.stats()
        db_ok = await db.ping()
        await safe_send(message.bot, message.chat.id,
            "<b>Status</b>\n"
            f"MongoDB: {'🟢 up' if db_ok else '🔴 down'}\n"
            f"Model: <code>{escape(settings.ai_model)}</code>\n"
            f"Workers: {settings.worker_count}\n\n"
            "<b>Queue</b>\n"
            f"Pending: {stats.get('pending',0)}  |  Running: {stats.get('leased',0)}\n"
            f"Done: {stats.get('done',0)}  |  Failed: {stats.get('failed',0)}")

    @dp.message(Command("reset"))
    async def on_reset(message: Message) -> None:
        if not await _guard(message):
            return
        n = await db.clear_history(message.chat.id)
        await safe_send(message.bot, message.chat.id, f"🧹 Cleared {n} memory entries for this chat.")

    @dp.message(Command("cancel"))
    async def on_cancel(message: Message) -> None:
        if not await _guard(message):
            return
        n = await db.cancel_chat_tasks(message.chat.id)
        await safe_send(message.bot, message.chat.id, f"🛑 Cancelled {n} queued/running task(s).")

    @dp.message(F.text & ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        if not await _guard(message):
            return
        text = (message.text or "").strip()
        if not text:
            return
        if len(text) > settings.max_task_chars:
            text = truncate(text, settings.max_task_chars)
        task_id = await db.enqueue_task(
            chat_id=message.chat.id,
            user_id=message.from_user.id if message.from_user else 0,
            text=text,
            message_id=message.message_id,
        )
        active = await db.pending_ahead(message.chat.id)
        if active > settings.worker_count:
            await safe_send(message.bot, message.chat.id,
                            f"⏳ Queued — {active - settings.worker_count} task(s) ahead of you.",
                            reply_to=message.message_id)
        log.info("Enqueued task %s for chat %s", task_id, message.chat.id)

    @dp.message()
    async def on_other(message: Message) -> None:
        if not await _guard(message):
            return
        await safe_send(message.bot, message.chat.id,
                        "I can only process text tasks for now. Please send me text.")
