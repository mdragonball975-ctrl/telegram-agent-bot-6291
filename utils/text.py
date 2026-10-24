"""Text helpers: safe chunking for Telegram, truncation, HTML escaping."""
from __future__ import annotations

import html


def escape(text: str) -> str:
    return html.escape(text or "", quote=False)


def truncate(text: str, limit: int, marker: str = "\n…[truncated]") -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(marker))
    return text[:keep] + marker


def chunk_text(text: str, limit: int = 4000) -> list[str]:
    """Split text into Telegram-sized chunks, preferring newline/space boundaries."""
    text = text or ""
    if not text.strip():
        return ["(empty response)"]
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    if text.strip():
        chunks.append(text)
    return chunks
