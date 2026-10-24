"""Logging with secret redaction so tokens never land in logs."""
from __future__ import annotations

import logging
import re
import sys

_SECRET_PATTERNS = [
    re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}"),          # Telegram bot tokens
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),                 # Groq keys
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),                # OpenAI-style keys
    re.compile(r"(mongodb(?:\+srv)?://[^:/\s]+:)[^@\s]+(@)"),  # Mongo passwords
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = redact(msg)
        if red != msg:
            record.msg, record.args = red, ()
        return True


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        if pat.groups == 2:
            text = pat.sub(r"\1***\2", text)
        else:
            text = pat.sub("***REDACTED***", text)
    return text


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))
    # Third-party noise: keep at INFO/WARNING even when we run DEBUG.
    for noisy in ("httpx", "httpcore", "aiogram.event", "pymongo", "uvicorn.access", "motor"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
