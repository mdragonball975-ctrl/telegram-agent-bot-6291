import asyncio
from datetime import timedelta

import pytest
from mongomock_motor import AsyncMongoMockClient

from db.mongo import Database, _utcnow


@pytest.fixture
def db():
    d = Database("mongodb://x", "t")
    d._client = AsyncMongoMockClient(tz_aware=True)
    d._db = d._client["t"]
    return d


@pytest.mark.asyncio
async def test_claim_is_exclusive_and_fifo(db):
    a = await db.enqueue_task(1, 1, "first")
    b = await db.enqueue_task(1, 1, "second")
    t1 = await db.claim_task("w1", 60, 3)
    t2 = await db.claim_task("w2", 60, 3)
    t3 = await db.claim_task("w3", 60, 3)
    assert str(t1["_id"]) == a and str(t2["_id"]) == b and t3 is None
    assert t1["attempts"] == 1 and t1["status"] == "leased"


@pytest.mark.asyncio
async def test_expired_lease_reclaimed(db):
    await db.enqueue_task(1, 1, "x")
    t = await db.claim_task("w1", 60, 3)
    await db.db.tasks.update_one({"_id": t["_id"]}, {"$set": {"lease_until": _utcnow() - timedelta(seconds=1)}})
    t2 = await db.claim_task("w2", 60, 3)
    assert t2 is not None and t2["worker"] == "w2" and t2["attempts"] == 2


@pytest.mark.asyncio
async def test_retry_delay_and_dead_letter(db):
    await db.enqueue_task(1, 1, "x")
    t = await db.claim_task("w", 60, 2)
    await db.fail_task(t["_id"], "err", dead=False, retry_delay=3600)
    assert await db.claim_task("w", 60, 2) is None  # delayed
    await db.db.tasks.update_one({"_id": t["_id"]}, {"$set": {"lease_until": None}})
    t = await db.claim_task("w", 60, 2)
    assert t["attempts"] == 2
    await db.fail_task(t["_id"], "err", dead=True)
    assert (await db.db.tasks.find_one({"_id": t["_id"]}))["status"] == "failed"


@pytest.mark.asyncio
async def test_reaper_and_cancel(db):
    await db.enqueue_task(1, 1, "x")
    t = await db.claim_task("w", 60, 1)
    await db.db.tasks.update_one({"_id": t["_id"]}, {"$set": {"lease_until": _utcnow() - timedelta(seconds=1)}})
    assert await db.claim_task("w", 60, 1) is None
    assert await db.reap_exhausted(1) == 1
    await db.enqueue_task(2, 1, "y")
    assert await db.cancel_chat_tasks(2) == 1
    assert await db.claim_task("w", 60, 5) is None


@pytest.mark.asyncio
async def test_history_order_and_limit(db):
    for i in range(5):
        await db.add_history(9, "user", f"m{i}")
        await asyncio.sleep(0.002)
    h = await db.get_history(9, 3)
    assert [x["content"] for x in h] == ["m2", "m3", "m4"]
    assert await db.get_history(9, 0) == []
    assert await db.clear_history(9) == 5
