"""consumer.py — MQTT → 遥测管线消费者
=====================================
订阅 {line}/+/+/telemetry，按设备聚合 cycle 批量写入：
  - HttpSink（生产）: 服务账号登录拿 JWT → POST /api/v1/devices/{id}/telemetry
                     （复用卡死传感器校验 + 幂等去重 + 班组长鉴权）
  - DbSink（本地/联调）: 直写 device_telemetry，按 (device_id, cycle) 幂等

去抖：PLC 信号冻结时连续 cycle 的特征值完全相同，批量会触发服务端
"卡死传感器"整批拒绝——消费端按设备跳过与上一行完全相同的行。
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Protocol

logger = logging.getLogger("telemetry_consumer")


class Sink(Protocol):
    async def write(self, device_id: str, rows: list[dict]) -> int: ...


class DbSink:
    """直写库（绕过 HTTP，用于本机联调/集成测试；幂等跳过已有 cycle）。"""

    async def write(self, device_id: str, rows: list[dict]) -> int:
        from sqlalchemy import select

        from src.core.database import get_session_factory
        from src.data.models import DeviceTelemetry

        factory = get_session_factory()
        async with factory() as db:
            existing = set((await db.execute(
                select(DeviceTelemetry.cycle).where(DeviceTelemetry.device_id == device_id)
                .where(DeviceTelemetry.cycle.in_([r["cycle"] for r in rows]))
            )).scalars().all())
            fresh = [r for r in rows if r["cycle"] not in existing]
            for r in fresh:
                cycle = r.pop("cycle")
                db.add(DeviceTelemetry(device_id=device_id, cycle=cycle, readings=r))
            await db.commit()
        return len(fresh)


class HttpSink:
    """生产模式：服务账号 → JWT → FastAPI 遥测路由。"""

    def __init__(self, base_url: str, username: str, password: str):
        import httpx
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)
        self._username = username
        self._password = password
        self._token: str | None = None

    async def _login(self) -> None:
        resp = await self._client.post("/api/v1/auth/login",
                                       json={"username": self._username, "password": self._password})
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"服务账号登录失败: {body}")
        self._token = body["data"]["token"]

    async def write(self, device_id: str, rows: list[dict]) -> int:
        if self._token is None:
            await self._login()
        resp = await self._client.post(
            f"/api/v1/devices/{device_id}/telemetry",
            json={"readings": rows},
            headers={"Authorization": f"Bearer {self._token}"},
        )
        if resp.status_code == 401:                      # token 过期，重登一次重试
            await self._login()
            resp = await self._client.post(
                f"/api/v1/devices/{device_id}/telemetry",
                json={"readings": rows},
                headers={"Authorization": f"Bearer {self._token}"},
            )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return data.get("inserted", 0)

    async def close(self) -> None:
        await self._client.aclose()


class TelemetryConsumer:
    def __init__(self, sink: Sink, line: str, flush_interval: float = 2.0, max_batch: int = 60):
        self.sink = sink
        self.line = line
        self.flush_interval = flush_interval
        self.max_batch = max_batch
        self._pending: dict[str, list[dict]] = {}
        self._last_features: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.received = 0
        self.written = 0
        self.skipped_frozen = 0

    def topic_filter(self) -> str:
        return f"{self.line}/+/+/telemetry"

    def on_message(self, topic: str, payload: bytes) -> None:
        device_id = topic.split("/")[2]
        msg = json.loads(payload)
        row = {"cycle": int(msg["cycle"]), **(msg.get("readings") or {})}
        with self._lock:
            last = self._last_features.get(device_id)
            features = {k: v for k, v in row.items() if k != "cycle"}
            if last == features:
                self.skipped_frozen += 1
                return
            self._last_features[device_id] = features
            self._pending.setdefault(device_id, []).append(row)
            self.received += 1

    async def flush(self) -> None:
        with self._lock:
            batches = {d: rows for d, rows in self._pending.items() if rows}
            self._pending = {d: rows for d, rows in self._pending.items() if not rows}
        for device_id, rows in batches.items():
            try:
                n = await self.sink.write(device_id, rows)
                self.written += n
                logger.info(f"{device_id}: 写入 {n}/{len(rows)} 条（其余为重复 cycle）")
            except Exception as e:
                logger.error(f"{device_id} 写入失败: {e}")
                with self._lock:
                    self._pending.setdefault(device_id, []).extend(rows)   # 下轮重试
