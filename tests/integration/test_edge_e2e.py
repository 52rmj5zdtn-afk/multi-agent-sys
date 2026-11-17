# -*- coding: utf-8 -*-
"""边缘链路端到端集成测试（进程内 amqtt broker，无外部依赖）。

仿真 PLC 源 → edge_collector(MQTT QoS1) → broker → telemetry_consumer(DbSink)
→ device_telemetry：断言 4 台设备 × N 拍的特征行落库、cycle 连续、
DbSink 按 (device_id, cycle) 幂等。
"""
import asyncio
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.edge_collector.collector import Collector, MqttPublisher
from services.edge_collector.simulator import SimulatedSource
from services.edge_collector.tags import load_tag_table
from services.telemetry_consumer.consumer import DbSink, TelemetryConsumer


def _free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def mqtt_broker():
    import threading

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder: dict = {}

    def _serve() -> None:
        asyncio.set_event_loop(loop)

        async def _start() -> None:
            from amqtt.broker import Broker
            port = _free_port()
            broker = Broker({"listeners": {"default": {"type": "tcp",
                                                        "bind": f"127.0.0.1:{port}",
                                                        "max_connections": -1}},
                             "sys_interval": 0})
            await broker.start()
            holder["broker"], holder["port"] = broker, port
            holder["task"] = asyncio.current_task()
            ready.set()
            await asyncio.Event().wait()          # 持续服务直到被取消

        loop.run_until_complete(_start())

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    assert ready.wait(15), "amqtt broker 启动超时"
    yield holder["port"]

    async def _shutdown() -> None:
        await holder["broker"].shutdown()

    try:
        asyncio.run_coroutine_threadsafe(_shutdown(), loop).result(8)
    except Exception:
        pass                                # broker 线程是 daemon，不阻塞测试退出
    try:
        loop.call_soon_threadsafe(holder["task"].cancel)
    except Exception:
        pass
    thread.join(timeout=3)


@pytest.fixture()
def weld_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/edge.db")
    import src.core.database as database
    database._engine = None
    database._session_factory = None
    from src.core.config import get_settings
    get_settings.cache_clear()
    from src.core.database import init_db
    asyncio.run(init_db())
    return tmp_path


async def _subscribe(consumer: TelemetryConsumer, host: str, port: int):
    import paho.mqtt.client as mqtt
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="test-consumer")
    loop = asyncio.get_running_loop()
    done = loop.create_future()

    def on_connect(c, u, f, rc, p):
        if rc == 0 and not done.done() and not loop.is_closed():
            c.subscribe(consumer.topic_filter(), qos=1)
            try:
                loop.call_soon_threadsafe(done.set_result, True)   # paho 回调在网路线程
            except RuntimeError:
                pass                                               # 场景循环已关闭（拆卸期）
    client.on_connect = on_connect
    client.on_message = lambda c, u, msg: consumer.on_message(msg.topic, msg.payload)
    client.connect(host, port, keepalive=30)
    client.loop_start()
    await done
    return client


def test_edge_to_telemetry_e2e(mqtt_broker, weld_db):
    table = load_tag_table()
    source = SimulatedSource(table, cycles=60, hz=50, seed=11)     # 20ms/拍加速
    publisher = MqttPublisher("127.0.0.1", mqtt_broker, client_id="test-collector")

    consumer = TelemetryConsumer(DbSink(), line="line03")

    async def scenario() -> None:
        client = await _subscribe(consumer, "127.0.0.1", mqtt_broker)
        try:
            await asyncio.sleep(0.5)                # 等 SUBACK 生效，避免首拍消息竞态丢失
            collector = Collector(source, publisher, table)
            await collector.run(max_cycles=8)
            await asyncio.sleep(consumer.flush_interval + 0.5)      # 等消息到达
            await consumer.flush()
        finally:
            publisher.close()
            client.loop_stop()
            client.disconnect()
    asyncio.run(asyncio.wait_for(scenario(), timeout=60))

    n_dev = len(table.devices)          # 期望值随标签表设备数自适应（真实PLC版为1台）
    assert consumer.received >= 8 * n_dev, f"MQTT 消息丢失: {consumer.received}"
    assert consumer.written >= 8 * n_dev

    from sqlalchemy import func, select

    from src.core.database import get_session_factory
    from src.data.models import DeviceTelemetry

    async def verify() -> dict[str, tuple[int, int]]:
        async with get_session_factory()() as db:
            out = {}
            for device_id in table.devices:
                n, cyc_max = (await db.execute(
                    select(func.count(), func.max(DeviceTelemetry.cycle))
                    .where(DeviceTelemetry.device_id == device_id)
                )).one()
                out[device_id] = (n, cyc_max)
            return out
    stats = asyncio.run(verify())
    for device_id, (n, cyc_max) in stats.items():
        assert n == 8, f"{device_id}: {n} rows"
        assert cyc_max == 8
    laser_rows_n = stats["line03-weld-laser-01"][0]

    # DbSink 幂等：重复写同批 → 0 新增
    async def rewrite() -> int:
        sink = DbSink()
        return await sink.write("line03-weld-laser-01",
                                [{"cycle": 1, "laser_power_setpoint_w": 4000.0}])
    assert asyncio.run(rewrite()) == 0
    assert stats["line03-weld-laser-01"][0] == laser_rows_n
