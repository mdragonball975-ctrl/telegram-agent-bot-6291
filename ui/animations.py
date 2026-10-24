"""Live progress animation frames for Telegram message edits.

Telegram rate-limits edits, so the worker throttles how often these are shown
(PROGRESS_INTERVAL_SECONDS, min 2s). We cycle through readable status frames.
"""
from __future__ import annotations

import itertools

STAGES = [
    "🧠 Deep Analyzing…",
    "⚡ Processing…",
    "🛠️ Sandbox Executing…",
    "🔎 Verifying…",
]

SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class ProgressAnimator:
    def __init__(self) -> None:
        self._spin = itertools.cycle(SPINNER)
        self._stage = 0
        self._detail = ""

    def set_stage(self, index: int) -> None:
        self._stage = max(0, min(index, len(STAGES) - 1))

    def advance_stage(self) -> None:
        self._stage = min(self._stage + 1, len(STAGES) - 1)

    def set_detail(self, detail: str) -> None:
        self._detail = detail

    def frame(self) -> str:
        bar = "".join("●" if i <= self._stage else "○" for i in range(len(STAGES)))
        lines = [f"{next(self._spin)} <b>{STAGES[self._stage]}</b>", bar]
        if self._detail:
            lines.append(f"<i>{self._detail}</i>")
        return "\n".join(lines)

    @staticmethod
    def done_frame(elapsed: float) -> str:
        return f"✅ <b>Execution Verified</b>\n●●●●\n<i>Completed in {elapsed:.1f}s</i>"

    @staticmethod
    def error_frame(message: str) -> str:
        return f"❌ <b>Execution Failed</b>\n<i>{message}</i>"
