"""buffer.py — SQLite 断线缓存补传（store-and-forward）
=====================================================
边缘层的核心可靠性件：MQTT 断连/发布失败时，消息落 SQLite；
恢复后按原始时间戳顺序补传，补传成功再删除。
进程重启不丢数据（SQLite 持久化在边缘 IPC 本地）。

    buf = SqliteBuffer("edge_buffer.db")
    buf.put(topic, payload, ts)          # 发布失败时调用
    for topic, payload, ts in buf.drain(100):   # 重连后补传
        if publish_ok(topic, payload):
            buf.ack(topic, ts)
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class SqliteBuffer:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS pending ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  topic TEXT NOT NULL, payload TEXT NOT NULL, ts REAL NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def put(self, topic: str, payload: dict, ts: float | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False)
        with self._lock, self._connect() as con:
            con.execute("INSERT INTO pending(topic, payload, ts) VALUES (?,?,?)",
                        (topic, body, ts or time.time()))

    def drain(self, limit: int = 200) -> list[tuple[int, str, dict, float]]:
        """取最早的一批待补传消息（不删除，补传成功需 ack）。"""
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT id, topic, payload, ts FROM pending ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
        return [(r[0], r[1], json.loads(r[2]), r[3]) for r in rows]

    def ack(self, msg_id: int) -> None:
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM pending WHERE id=?", (msg_id,))

    def __len__(self) -> int:
        with self._lock, self._connect() as con:
            return con.execute("SELECT COUNT(*) FROM pending").fetchone()[0]
