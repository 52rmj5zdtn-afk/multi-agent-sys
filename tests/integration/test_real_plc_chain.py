# -*- coding: utf-8 -*-
"""真实 S7-1500 虚拟 PLC 全链路集成测试（PLCSIM Advanced → OPC UA → MQTT → 时序库）。

前提（人工/自动化准备完成）：
  1. PLCSIM Advanced 实例 Line03PLC（CPU 1516TF）已装载 MiniPLC 项目并处于 RUN；
  2. tags_weld.yaml 已按真实 OPC UA 节点路径更新（browse_opcua.py 导出）；
  3. 本测试用 insecure 连接（TIA 中已启用 "No security" 策略，仅验证期使用）。

运行: pytest tests/integration/test_real_plc_chain.py -v
"""
from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.edge_collector.collector import Collector, MqttPublisher       # noqa: E402
from services.edge_collector.opcua_source import OpcuaSource                 # noqa: E402
from services.edge_collector.tags import load_tag_table                      # noqa: E402
from services.telemetry_consumer.consumer import DbSink, TelemetryConsumer   # noqa: E402

PLC_ENDPOINT = "opc.tcp://192.168.1.6:4840"


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
            await asyncio.Event().wait()

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
        pass
    try:
        loop.call_soon_threadsafe(holder["task"].cancel)
    except Exception:
        pass


@pytest.fixture()
def telemetry_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/real_plc_chain.db")
    import src.core.database as database
    database._engine = None
    database._session_factory = None
    from src.core.config import get_settings
    get_settings.cache_clear()
    from src.core.database import init_db
    asyncio.run(init_db())
    return tmp_path


def test_real_plc_opcua_to_telemetry_chain(mqtt_broker, telemetry_db):
    import socket
    _s = socket.socket()
    _s.settimeout(3)
    try:
        _s.connect(('192.168.1.6', 4840))
    except OSError:
        pytest.skip('仿真 PLC 不在线（192.168.1.6:4840 不可达）')
    finally:
        _s.close()
    from services.edge_collector.tags import DEFAULT_TAGS
    table = load_tag_table()
    source = OpcuaSource(table, endpoint=PLC_ENDPOINT, insecure=True)
    publisher = MqttPublisher("127.0.0.1", mqtt_broker, client_id="real-plc-collector")
    consumer = TelemetryConsumer(DbSink(), line=table.line)

    async def scenario() -> None:
        from tests.integration.test_edge_e2e import _subscribe
        client = await _subscribe(consumer, "127.0.0.1", mqtt_broker)
        try:
            await asyncio.sleep(0.5)
            collector = Collector(source, publisher, table)
            n = await collector.run(max_cycles=8)
            assert n > 0, "采集器未能从真实 PLC 发布任何消息"
            await asyncio.sleep(consumer.flush_interval + 0.5)
            await consumer.flush()
        finally:
            publisher.close()
            await source.close()
            client.loop_stop()
            client.disconnect()

    asyncio.run(asyncio.wait_for(scenario(), timeout=120))

    from sqlalchemy import func, select

    from src.core.database import get_session_factory
    from src.data.models import DeviceTelemetry

    async def per_device() -> dict[str, int]:
        async with get_session_factory()() as db:
            rows = (await db.execute(
                select(DeviceTelemetry.device_id, func.count())
                .group_by(DeviceTelemetry.device_id))).all()
            return {d: n for d, n in rows}

    counts = asyncio.run(per_device())
    assert counts, (f"链路无落库: written={consumer.written} "
                    f"received={consumer.received} skipped_frozen={consumer.skipped_frozen}")
    print(f"\n落库统计: {counts}; written={consumer.written} "
          f"received={consumer.received} frozen={consumer.skipped_frozen}")
