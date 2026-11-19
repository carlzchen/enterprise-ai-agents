"""MongoDB 会话历史：每条消息一个文档，(session_id, ts) 复合索引支持按会话倒序读取最近 N 条"""
import threading
from datetime import datetime

from pymongo import DESCENDING, MongoClient

from app.infra.config.settings import settings


class HistoryRepository:
    def __init__(self):
        self._col = None
        self._lock = threading.Lock()

    @property
    def col(self):
        with self._lock:
            if self._col is None:
                db = MongoClient(settings.mongo_url, serverSelectionTimeoutMS=3000)[settings.mongo_db]
                self._col = db["chat_message"]
                self._col.create_index([("session_id", 1), ("ts", DESCENDING)])
                self._col.create_index([("username", 1), ("ts", DESCENDING)])
        return self._col

    def save_message(self, *, session_id: str, username: str, role: str, text: str, rewritten_query: str = "",
                     item_names: list[str] | None = None, image_urls: list[str] | None = None,
                     sources: list[dict] | None = None) -> str:
        doc = {"session_id": session_id, "username": username, "role": role, "text": text,
               "rewritten_query": rewritten_query, "item_names": item_names or [], "image_urls": image_urls or [],
               "sources": sources or [], "ts": datetime.now().timestamp()}
        return str(self.col.insert_one(doc).inserted_id)

    def list_recent(self, session_id: str, limit: int = 10) -> list[dict]:
        """最近 limit 条，按时间正序返回"""
        rows = list(self.col.find({"session_id": session_id}).sort("ts", DESCENDING).limit(limit))
        return list(reversed(rows))

    def list_sessions(self, username: str, limit: int = 20) -> list[dict]:
        pipeline = [{"$match": {"username": username, "role": "user"}}, {"$sort": {"ts": -1}},
                    {"$group": {"_id": "$session_id", "last_ts": {"$first": "$ts"}, "title": {"$last": "$text"}}},
                    {"$sort": {"last_ts": -1}}, {"$limit": limit}]
        return [{"session_id": r["_id"], "last_ts": r["last_ts"], "title": r["title"][:40]} for r in self.col.aggregate(pipeline)]

    def session_owner(self, session_id: str) -> str | None:
        doc = self.col.find_one({"session_id": session_id}, {"username": 1})
        return doc.get("username") if doc else None

    def clear_session(self, session_id: str) -> int:
        return self.col.delete_many({"session_id": session_id}).deleted_count


history_repository = HistoryRepository()
