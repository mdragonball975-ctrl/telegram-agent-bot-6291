"""Worker pool: claims tasks from Mongo with leases, runs the agent, reports back."""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
from pathlib import Path

from aiogram import Bot

from bot.sender import log_to_channel, safe_delete, safe_edit, safe_send, send_long
from config import Settings
from core.agent import Agent
from core.ai_client import AIClient
from core.sandbox import Sandbox
from core.tools import ToolExecutor
from db.mongo import Database
from ui.animations import ProgressAnimator
from utils.text import escape, truncate

log = logging.getLogger(__name__)


class WorkerPool:
    def __init__(self, bot: Bot, db: Database, ai: AIClient, settings: Settings) -> None:
        self.bot = bot
        self.db = db
        self.ai = ai
        self.s = settings
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self.last_tick = time.monotonic()

    async def start(self) -> None:
        Path(self.s.workspace_dir).mkdir(parents=True, exist_ok=True)
        for i in range(self.s.worker_count):
            self._tasks.append(asyncio.create_task(self._worker_loop(f"worker-{i}"), name=f"worker-{i}"))
        self._tasks.append(asyncio.create_task(self._reaper_loop(), name="reaper"))
        log.info("Started %d workers", self.s.worker_count)

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def healthy(self) -> bool:
        alive = all(not t.done() for t in self._tasks)
        return alive and (time.monotonic() - self.last_tick) < max(60, self.s.poll_seconds * 30)

    # ------------------------------------------------------------------
    async def _reaper_loop(self) -> None:
        while not self._stop.is_set():
            try:
                n = await self.db.reap_exhausted(self.s.max_attempts)
                if n:
                    log.warning("Reaped %d exhausted task(s)", n)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Reaper error")
            await asyncio.sleep(60)

    async def _worker_loop(self, name: str) -> None:
        backoff = self.s.poll_seconds
        while not self._stop.is_set():
            self.last_tick = time.monotonic()
            try:
                task = await self.db.claim_task(name, self.s.lease_seconds, self.s.max_attempts)
                backoff = self.s.poll_seconds
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # Mongo blip: back off, never die
                log.warning("%s claim failed: %s", name, exc)
                backoff = min(30.0, backoff * 2)
                await asyncio.sleep(backoff)
                continue

            if task is None:
                await asyncio.sleep(self.s.poll_seconds)
                continue

            try:
                await self._process(name, task)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s: unhandled error processing task", name)

    async def _process(self, worker: str, task: dict) -> None:
        task_id = task["_id"]
        chat_id = int(task["chat_id"])
        reply_to = task.get("message_id")
        started = time.monotonic()
        animator = ProgressAnimator()

        status_msg = await safe_send(self.bot, chat_id, animator.frame(), reply_to=reply_to)
        status_id = status_msg.message_id if status_msg else None

        stop_anim = asyncio.Event()

        async def animate() -> None:
            tick = 0
            while not stop_anim.is_set():
                try:
                    await asyncio.wait_for(stop_anim.wait(), timeout=self.s.progress_interval_seconds)
                    break
                except asyncio.TimeoutError:
                    pass
                tick += 1
                if tick % 3 == 0 and animator._stage < 1:
                    animator.advance_stage()
                if status_id is not None:
                    await safe_edit(self.bot, chat_id, status_id, animator.frame())

        async def heartbeat() -> None:
            interval = max(5, min(self.s.heartbeat_seconds, self.s.lease_seconds // 3))
            while not stop_anim.is_set():
                try:
                    await asyncio.wait_for(stop_anim.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    pass
                try:
                    await self.db.renew_lease(task_id, worker, self.s.lease_seconds)
                except Exception as exc:
                    log.warning("Lease renewal failed for %s: %s", task_id, exc)

        async def progress(detail: str) -> None:
            animator.set_stage(2)
            animator.set_detail(detail)

        anim_task = asyncio.create_task(animate())
        hb_task = asyncio.create_task(heartbeat())
        try:
            history = await self.db.get_history(chat_id, self.s.history_limit)
            sandbox = Sandbox(self.s.workspace_dir, chat_id, self.s.max_file_bytes)
            tools = ToolExecutor(sandbox, self.s.max_tool_result_chars, self.s.python_exec_timeout)
            agent = Agent(self.ai, tools, self.s.max_agent_steps)

            run = asyncio.create_task(agent.run(task["text"], history, progress))
            # Hard ceiling so a stuck task can never hold a worker forever.
            deadline = self.s.lease_seconds * 3
            while not run.done():
                done, _ = await asyncio.wait({run}, timeout=5)
                if done:
                    break
                if await self.db.is_cancelled(task_id):
                    run.cancel()
                    raise _Cancelled()
                if time.monotonic() - started > deadline:
                    run.cancel()
                    raise TimeoutError(f"task exceeded {deadline}s")
            answer = run.result()

            stop_anim.set()
            if status_id is not None:
                await safe_edit(self.bot, chat_id, status_id,
                                ProgressAnimator.done_frame(time.monotonic() - started))
            await send_long(self.bot, chat_id, answer, self.s.telegram_message_max_length, reply_to)

            await self.db.add_history(chat_id, "user", truncate(task["text"], 8000))
            await self.db.add_history(chat_id, "assistant", truncate(answer, 8000))
            await self.db.complete_task(task_id, truncate(answer, 20000))
            log.info("Task %s done in %.1fs", task_id, time.monotonic() - started)

        except _Cancelled:
            stop_anim.set()
            if status_id is not None:
                await safe_edit(self.bot, chat_id, status_id, "🛑 <b>Cancelled</b>")
        except Exception as exc:
            stop_anim.set()
            attempts = int(task.get("attempts", 1))
            dead = attempts >= self.s.max_attempts
            err = f"{type(exc).__name__}: {exc}"
            log.error("Task %s failed (attempt %d/%d): %s", task_id, attempts, self.s.max_attempts, err)
            try:
                await self.db.fail_task(task_id, err, dead, retry_delay=min(300, 5 * 2 ** (attempts - 1)))
            except Exception:
                log.exception("Could not record failure")
            if status_id is not None:
                if dead:
                    await safe_edit(self.bot, chat_id, status_id, ProgressAnimator.error_frame(escape(err[:300])))
                else:
                    await safe_delete(self.bot, chat_id, status_id)  # a retry will post a fresh one
            await log_to_channel(self.bot, self.s.log_channel,
                f"⚠️ <b>Task failed</b> (attempt {attempts}/{self.s.max_attempts})\n"
                f"chat: <code>{chat_id}</code>\n<pre>{escape(truncate(traceback.format_exc(), 3000))}</pre>")
        finally:
            stop_anim.set()
            for t in (anim_task, hb_task):
                t.cancel()
            await asyncio.gather(anim_task, hb_task, return_exceptions=True)


class _Cancelled(Exception):
    pass
