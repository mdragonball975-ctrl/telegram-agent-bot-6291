"""Resilient Telegram send/edit helpers (flood-wait aware, never raise)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import Message

from utils.text import chunk_text, escape

log = logging.getLogger(__name__)


async def safe_send(bot: Bot, chat_id: int, text: str, *, html: bool = True,
                    reply_to: int | None = None, retries: int = 4) -> Message | None:
    for attempt in range(retries):
        try:
            return await bot.send_message(
                chat_id, text,
                parse_mode="HTML" if html else None,
                reply_to_message_id=reply_to,
                allow_sending_without_reply=True,
                disable_web_page_preview=True,
            )
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 0.5)
        except TelegramBadRequest as exc:
            if html and "parse" in str(exc).lower():
                html = False  # fall back to plain text if HTML is malformed
                continue
            if reply_to is not None and "reply" in str(exc).lower():
                reply_to = None
                continue
            log.warning("send_message bad request: %s", exc)
            return None
        except TelegramForbiddenError:
            log.warning("Bot blocked or removed from chat %s", chat_id)
            return None
        except TelegramNetworkError:
            await asyncio.sleep(2 ** attempt)
        except Exception:
            log.exception("Unexpected send error")
            await asyncio.sleep(1)
    return None


async def safe_edit(bot: Bot, chat_id: int, message_id: int, text: str) -> bool:
    for attempt in range(3):
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                        parse_mode="HTML", disable_web_page_preview=True)
            return True
        except TelegramRetryAfter as exc:
            if exc.retry_after > 30:
                return False  # skip this frame rather than block the worker
            await asyncio.sleep(exc.retry_after + 0.5)
        except TelegramBadRequest as exc:
            if "not modified" in str(exc).lower():
                return True
            return False
        except TelegramNetworkError:
            await asyncio.sleep(1 + attempt)
        except Exception:
            return False
    return False


async def safe_delete(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def send_long(bot: Bot, chat_id: int, text: str, limit: int, reply_to: int | None = None) -> None:
    """Send a (possibly long) model answer as plain-escaped HTML chunks."""
    for i, chunk in enumerate(chunk_text(text, limit)):
        await safe_send(bot, chat_id, escape(chunk), reply_to=reply_to if i == 0 else None)


async def log_to_channel(bot: Bot, channel: int | None, text: str) -> None:
    if channel is None:
        return
    try:
        await bot.send_message(channel, text[:4000], parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        log.debug("Log channel send failed: %s", exc)
