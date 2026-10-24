"""Async MongoDB (Atlas) access via Motor, with an atomic lease-based task queue."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, ReturnDocument

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    """Thin, fault-tolerant wrapper around the Mongo collections we need."""

    def __init__(self, uri: str, db_name: str) -> None:
        self._uri = uri
        self._db_name = db_name
        self._client: AsyncIOMotorClient | None = None
        self._db: AsyncIOMotorDatabase | None = None

    @property
    def db(self) -> AsyncIOMotorDatabase:
        if self._db is None:
            raise RuntimeError("Database.connect() must be called first")
        return self._db

    async def connect(self) -> None:
        self._client = AsyncIOMotorClient(
            self._uri,
            serverSelectionTimeoutMS=15_000,
            connectTimeoutMS=15_000,
            retryWrites=True,
            tz_aware=True,
        )
        # Fail fast if credentials/network are wrong.
        await self._client.admin.command("ping")
        self._db = self._client[self._db_name]
        await self._ensure_indexes()
        log.info("Connected to MongoDB database '%s'", self._db_name)

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None

    async def _ensure_indexes(self) -> None:
        await self.db.tasks.create_index([("status", ASCENDING), ("lease_until", ASCENDING)])
        await self.db.tasks.create_index([("created_at", ASCENDING)])
        await self.db.history.create_index([("chat_id", ASCENDING), ("created_at", DESCENDING)])

    # ---------------- Task queue ----------------
    async def enqueue_task(self, chat_id: int, user_id: int, text: str, message_id: int | None = None) -> str:
        doc = {
            "chat_id": chat_id,
            "user_id": user_id,
            "message_id": message_id,
            "text": text,
            "status": "pending",
            "attempts": 0,
            "lease_until": None,
            "worker": None,
            "result": None,
            "error": None,
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
        }
        res = await self.db.tasks.insert_one(doc)
        return str(res.inserted_id)

    async def claim_task(self, worker: str, lease_seconds: int, max_attempts: int) -> dict[str, Any] | None:
        """Atomically claim one runnable task (new or expired lease)."""
        now = _utcnow()
        lease_until = now + timedelta(seconds=lease_seconds)
        query = {
            "status": {"$in": ["pending", "leased"]},
            "attempts": {"$lt": max_attempts},
            "$or": [{"lease_until": None}, {"lease_until": {"$lte": now}}],
        }
        update = {
            "$set": {"status": "leased", "worker": worker, "lease_until": lease_until, "updated_at": now},
            "$inc": {"attempts": 1},
        }
        return await self.db.tasks.find_one_and_update(
            query, update, sort=[("created_at", ASCENDING)], return_document=ReturnDocument.AFTER
        )

    async def renew_lease(self, task_id, worker: str, lease_seconds: int) -> None:
        from bson import ObjectId

        await self.db.tasks.update_one(
            {"_id": ObjectId(str(task_id)), "worker": worker, "status": "leased"},
            {"$set": {"lease_until": _utcnow() + timedelta(seconds=lease_seconds), "updated_at": _utcnow()}},
        )

    async def complete_task(self, task_id, result: str) -> None:
        from bson import ObjectId

        await self.db.tasks.update_one(
            {"_id": ObjectId(str(task_id))},
            {"$set": {"status": "done", "result": result, "lease_until": None, "updated_at": _utcnow()}},
        )

    async def fail_task(self, task_id, error: str, dead: bool, retry_delay: float = 0) -> None:
        from bson import ObjectId

        retry_at = _utcnow() + timedelta(seconds=retry_delay) if (not dead and retry_delay > 0) else None

        await self.db.tasks.update_one(
            {"_id": ObjectId(str(task_id))},
            {"$set": {
                "status": "failed" if dead else "pending",
                "error": error,
                "worker": None,
                "lease_until": retry_at,
                "updated_at": _utcnow(),
            }},
        )

    async def cancel_chat_tasks(self, chat_id: int) -> int:
        res = await self.db.tasks.update_many(
            {"chat_id": chat_id, "status": {"$in": ["pending", "leased"]}},
            {"$set": {"status": "cancelled", "lease_until": None, "updated_at": _utcnow()}},
        )
        return res.modified_count

    async def is_cancelled(self, task_id) -> bool:
        from bson import ObjectId

        doc = await self.db.tasks.find_one({"_id": ObjectId(str(task_id))}, {"status": 1})
        return doc is None or doc.get("status") == "cancelled"

    async def reap_exhausted(self, max_attempts: int) -> int:
        """Tasks whose worker died on the final attempt would otherwise sit forever."""
        res = await self.db.tasks.update_many(
            {"status": {"$in": ["pending", "leased"]}, "attempts": {"$gte": max_attempts},
             "$or": [{"lease_until": None}, {"lease_until": {"$lte": _utcnow()}}]},
            {"$set": {"status": "failed", "error": "max attempts exhausted", "updated_at": _utcnow()}},
        )
        return res.modified_count

    async def pending_ahead(self, chat_id: int) -> int:
        return await self.db.tasks.count_documents({"status": {"$in": ["pending", "leased"]}})

    # ---------------- Conversation history ----------------
    async def add_history(self, chat_id: int, role: str, content: str) -> None:
        await self.db.history.insert_one(
            {"chat_id": chat_id, "role": role, "content": content, "created_at": _utcnow()}
        )

    async def get_history(self, chat_id: int, limit: int) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        cur = self.db.history.find({"chat_id": chat_id}).sort("created_at", DESCENDING).limit(limit)
        rows = await cur.to_list(length=limit)
        rows.reverse()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    async def clear_history(self, chat_id: int) -> int:
        res = await self.db.history.delete_many({"chat_id": chat_id})
        return res.deleted_count

    async def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for status in ("pending", "leased", "done", "failed", "cancelled"):
            out[status] = await self.db.tasks.count_documents({"status": status})
        return out

    async def ping(self) -> bool:
        try:
            if self._client is None:
                return False
            await self._client.admin.command("ping")
            return True
        except Exception:
            return False
