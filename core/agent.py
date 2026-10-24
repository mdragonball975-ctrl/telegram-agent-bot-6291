"""The autonomous reasoning loop: LLM + tools, bounded, self-correcting."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from core.ai_client import AIClient, AIClientError
from core.tools import TOOL_SCHEMAS, ToolExecutor

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an autonomous AI agent operating inside a secure Telegram bot. "
    "You can reason over multiple steps and call tools to read/write files in a sandbox, "
    "run Python, and fetch public URLs. Plan, act, observe the tool results, and correct yourself. "
    "Use tools only when they help; otherwise answer directly. When the task is complete, "
    "produce a clear, final, self-contained answer for the user with no tool calls. "
    "Be concise and accurate. Never invent tool output."
)

ProgressCb = Callable[[str], Awaitable[None]]


class Agent:
    def __init__(self, ai: AIClient, tools: ToolExecutor, max_steps: int) -> None:
        self.ai = ai
        self.tools = tools
        self.max_steps = max_steps

    async def run(
        self,
        task: str,
        history: list[dict[str, str]],
        progress: ProgressCb | None = None,
    ) -> str:
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": task})

        for step in range(1, self.max_steps + 1):
            try:
                reply = await self.ai.chat(messages, tools=TOOL_SCHEMAS)
            except AIClientError as exc:
                log.error("AI error on step %d: %s", step, exc)
                return f"⚠️ The AI service is currently unavailable. Please try again shortly.\n({exc})"

            tool_calls = reply.get("tool_calls") or []
            if not tool_calls:
                content = (reply.get("content") or "").strip()
                return content or "(The agent finished but produced no text.)"

            # Record the assistant's tool-call turn verbatim.
            messages.append({
                "role": "assistant",
                "content": reply.get("content") or "",
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                if progress is not None:
                    await progress(f"🛠️ Executing <code>{name}</code> (step {step})")
                result = await self.tools.execute(name, fn.get("arguments"))
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": name,
                    "content": result,
                })

        return "⚠️ Reached the maximum number of reasoning steps without a final answer. Try narrowing the task."
