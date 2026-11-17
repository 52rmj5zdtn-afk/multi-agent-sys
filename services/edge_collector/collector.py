"""collector.py — 边缘采集主循环
================================
每拍（默认 1Hz）：
  1. 源快照（OPC UA 实读 / 仿真步进）→ {device_id: {feature: value, cycle}}
  2. 每设备发布一条 MQTT 消息（QoS 1）：
       topic: {line}/{station}/{device_id}/telemetry
       payload: {"cycle": int, "ts": float, "readings": {feature: value}}
  3. 发布失败（断连）→ 落 SQLite，恢复后按原始顺序补传再 ack

M1 语义：每拍全量快照、无死区跳变（标签表保留 deadband 字段供 M2
高频扩展用）。时序完整性由 cycle 单调序号保证。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Protocol

from services.edge_collector.buffer import SqliteBuffer
from services.edge_collector.tags import TagTable

logger = logging.getLogger("edge_collector")


class Source(Protocol):
    async def read_snapshot(self) -> dict[str, dict[str, float]]: ...


class MqttPublisher:
    """paho-mqtt 封装：自动重连 + 可判定的发布结果（供补传逻辑使用）。"""

    def __init__(self, host: str, port: int = 1883, client_id: str = "edge-collector"):
        import paho.mqtt.client as mqtt
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self._connected = False
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.connect_async(host, port, keepalive=30)
        self._client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self._connected = reason_code == 0
        logger.info("MQTT 已连接" if self._connected else f"MQTT 连接被拒: {reason_code}")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self._connected = False
        logger.warning(f"MQTT 断开 rc={reason_code}，消息转入本地缓存")

    def publish(self, topic: str, payload: dict) -> bool:
        if not self._connected:
            return False
        info = self._client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1)
        return info.rc == 0

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


class Collector:
    def __init__(self, source: Source, publisher: MqttPublisher, table: TagTable,
                 buffer: SqliteBuffer | None = None, flush_batch: int = 200):
        self.source = source
        self.publisher = publisher
        self.table = table
        self.buffer = buffer
        self.flush_batch = flush_batch

    def topic(self, device_id: str) -> str:
        return f"{self.table.line}/{self.table.station}/{device_id}/telemetry"

    def _flush_buffer(self) -> None:
        if not self.buffer or len(self.buffer) == 0:
            return
        for msg_id, topic, payload, ts in self.buffer.drain(self.flush_batch):
            if self.publisher.publish(topic, payload):
                self.buffer.ack(msg_id)
            else:
                break                                # 仍断连，下拍再试

    async def run(self, max_cycles: int | None = None) -> int:
        """主循环；max_cycles 用于测试/干跑。返回成功发布的消息数。"""
        published = 0
        cycles_done = 0
        while max_cycles is None or cycles_done < max_cycles:
            snapshot = await self.source.read_snapshot()
            self._flush_buffer()
            for device_id, values in snapshot.items():
                cycle = int(values.pop("cycle"))
                payload = {"cycle": cycle, "ts": time.time(), "readings": values}
                if self.publisher.publish(self.topic(device_id), payload):
                    published += 1
                elif self.buffer is not None:
                    self.buffer.put(self.topic(device_id), payload, ts=payload["ts"])
                    logger.warning(f"{device_id} cycle={cycle} 发布失败，已缓存")
            cycles_done += 1
        return published
