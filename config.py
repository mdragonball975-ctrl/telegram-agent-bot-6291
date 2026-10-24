"""Central configuration. Every value comes from environment variables.

Secrets are NEVER hard-coded here: set them in Railway -> Variables
(or a local .env file that is git-ignored).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional local .env support
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _str(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.getenv(name, default)
    if val is not None:
        val = val.strip()
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val or ""


def _int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        val = int(raw) if raw not in (None, "") else default
    except ValueError:
        val = default
    if minimum is not None:
        val = max(minimum, val)
    if maximum is not None:
        val = min(maximum, val)
    return val


def _float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    try:
        val = float(raw) if raw not in (None, "") else default
    except ValueError:
        val = default
    if minimum is not None:
        val = max(minimum, val)
    return val


def _ids(name: str) -> frozenset[int]:
    out: set[int] = set()
    for part in os.getenv(name, "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return frozenset(out)


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(default_factory=lambda: _str("BOT_TOKEN", required=True))
    admin_ids: frozenset[int] = field(default_factory=lambda: _ids("ADMIN_IDS"))
    mongo_uri: str = field(default_factory=lambda: _str("MONGO_URI", required=True))
    mongo_database: str = field(default_factory=lambda: _str("MONGO_DATABASE", "private_agent"))

    ai_provider: str = field(default_factory=lambda: _str("AI_PROVIDER", "openai_compatible"))
    ai_api_key: str = field(default_factory=lambda: _str("AI_API_KEY") or _str("GROQ_API_KEY", required=True))
    ai_base_url: str = field(default_factory=lambda: _str("AI_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/"))
    ai_model: str = field(default_factory=lambda: _str("AI_MODEL", "llama-3.3-70b-versatile"))
    ai_temperature: float = field(default_factory=lambda: _float("AI_TEMPERATURE", 0.2, 0.0))
    ai_max_tokens: int = field(default_factory=lambda: _int("AI_MAX_TOKENS", 4096, 256, 32768))
    ai_max_rpm: int = field(default_factory=lambda: _int("AI_MAX_RPM", 30, 1))

    log_channel: int | None = field(default_factory=lambda: int(_str("LOG_CHANNEL")) if _str("LOG_CHANNEL").lstrip("-").isdigit() else None)
    update_channel: str = field(default_factory=lambda: _str("UPDATE_CHANNEL").lstrip("@"))
    # Kept for future MTProto (Pyrogram/Telethon) features; not required by the Bot API.
    api_id: str = field(default_factory=lambda: _str("API_ID"))
    api_hash: str = field(default_factory=lambda: _str("API_HASH"))

    workspace_dir: Path = field(default_factory=lambda: Path(_str("WORKSPACE_DIR", "/app/workspace")).resolve())
    worker_count: int = field(default_factory=lambda: _int("WORKER_COUNT", 4, 1, 64))
    poll_seconds: float = field(default_factory=lambda: _float("POLL_SECONDS", 1.0, 0.2))
    lease_seconds: int = field(default_factory=lambda: _int("LEASE_SECONDS", 600, 30))
    max_attempts: int = field(default_factory=lambda: _int("MAX_ATTEMPTS", 5, 1, 100))
    max_agent_steps: int = field(default_factory=lambda: _int("MAX_AGENT_STEPS", 30, 1, 500))
    max_file_bytes: int = field(default_factory=lambda: _int("MAX_FILE_BYTES", 10_000_000, 1024))
    max_task_chars: int = field(default_factory=lambda: _int("MAX_TASK_CHARS", 50_000, 100))
    # Telegram rate-limits message edits; anything below 2s causes 429 flood waits.
    progress_interval_seconds: float = field(default_factory=lambda: _float("PROGRESS_INTERVAL_SECONDS", 2.0, 2.0))
    heartbeat_seconds: int = field(default_factory=lambda: _int("HEARTBEAT_SECONDS", 180, 10))
    request_timeout_seconds: int = field(default_factory=lambda: _int("REQUEST_TIMEOUT_SECONDS", 120, 10))
    telegram_message_max_length: int = field(default_factory=lambda: _int("TELEGRAM_MESSAGE_MAX_LENGTH", 4000, 500, 4096))
    history_limit: int = field(default_factory=lambda: _int("HISTORY_LIMIT", 30, 0, 500))
    max_tool_result_chars: int = field(default_factory=lambda: _int("MAX_TOOL_RESULT_CHARS", 20_000, 500))
    python_exec_timeout: int = field(default_factory=lambda: _int("PYTHON_EXEC_TIMEOUT", 60, 5, 600))
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO").upper())
    port: int = field(default_factory=lambda: _int("PORT", 8080, 1, 65535))

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
