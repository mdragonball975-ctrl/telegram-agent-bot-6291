import asyncio
from types import SimpleNamespace

import httpx
import pytest
from aiogram import Dispatcher
from mongomock_motor import AsyncMongoMockClient

from bot.handlers import register_handlers
from config import Settings
from core.worker import WorkerPool
from db.mongo import Database
from health.server import build_app


class FakeBot:
    def __init__(self):
        self.sent, self.edits, self._id = [], [], 0
    async def send_message(self, chat_id, text, **kw):
        self._id += 1
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=self._id)
    async def edit_message_text(self, text, chat_id, message_id, **kw):
        self.edits.append(text)
    async def delete_message(self, *a, **k):
        pass


class FakeAI:
    async def chat(self, messages, tools=None, temperature=None):
        await asyncio.sleep(0.05)
        return {"content": "final <answer> & done"}


@pytest.mark.asyncio
async def test_worker_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("WORKER_COUNT", "2")
    s = Settings()
    db = Database("x", "t"); db._client = AsyncMongoMockClient(tz_aware=True); db._db = db._client["t"]
    bot = FakeBot()
    pool = WorkerPool(bot, db, FakeAI(), s)
    await db.enqueue_task(5, 5, "hello", message_id=1)
    await pool.start()
    for _ in range(100):
        if (await db.stats())["done"] == 1:
            break
        await asyncio.sleep(0.05)
    await pool.stop()
    assert (await db.stats())["done"] == 1
    assert any("final &lt;answer&gt; &amp; done" in t for _, t in bot.sent)  # escaped
    assert any("Verified" in e for e in bot.edits)
    assert len(await db.get_history(5, 10)) == 2


def test_handlers_register():
    s = Settings()
    db = Database("x", "t")
    register_handlers(Dispatcher(), db, s)


@pytest.mark.asyncio
async def test_health_endpoint():
    async def ok(): return {"ok": True}
    async def bad(): raise RuntimeError("x")
    for probe, code in ((ok, 200), (bad, 503)):
        app = build_app(probe)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/health")
            assert r.status_code == code
            assert (await c.get("/")).status_code == 200
