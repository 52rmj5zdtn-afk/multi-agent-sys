# -*- coding: utf-8 -*-
"""OPC UA（含加密+证书互信）全链路集成测试。

S7-1500 风格仿真服务器（asyncua，按 tags_weld.yaml 的符号 NodeId）
→ OpcuaSource（生产代码路径，Basic256Sha256_SignAndEncrypt）
→ collector → amqtt broker → consumer(DbSink) → device_telemetry。
证明"加密 + 证书互换"路径在生产代码上端到端可用。
"""
import asyncio
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.edge_collector.collector import Collector, MqttPublisher
from services.edge_collector.opcua_server_sim import SimOpcuaServer
from services.edge_collector.opcua_source import OpcuaSource
from services.edge_collector.tags import load_tag_table
from services.telemetry_consumer.consumer import DbSink, TelemetryConsumer


def _free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def opcua_server(tmp_path):
    """加密模式 S7-1500 风格仿真服务器（证书生成到 tmp，测试互不污染）。"""
    table = load_tag_table()
    port = _free_port()
    server = SimOpcuaServer(table, host="127.0.0.1", port=port, hz=50,
                            secure=True, cert_dir=tmp_path / "certs", cycles=120, seed=13)

    import threading
    loop_holder: dict = {}

    def _run() -> None:
        loop = asyncio.new_event_loop()
        loop_holder["loop"] = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        loop.run_forever()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            break
        except OSError:
            import time
            time.sleep(0.1)
    else:
        raise TimeoutError("OPC UA 仿真服务器启动超时")
    import time
    time.sleep(0.5)                       # 等安全策略初始化完成

    yield {"port": port, "certs": tmp_path / "certs"}

    async def _stop() -> None:
        await server.stop()

    try:
        asyncio.run_coroutine_threadsafe(_stop(), loop_holder["loop"]).result(8)
    except Exception:
        pass
    loop_holder["loop"].call_soon_threadsafe(loop_holder["loop"].stop)
    thread.join(timeout=3)


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
    thread.join(timeout=3)


@pytest.fixture()
def telemetry_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/opcua_chain.db")
    import src.core.database as database
    database._engine = None
    database._session_factory = None
    from src.core.config import get_settings
    get_settings.cache_clear()
    from src.core.database import init_db
    asyncio.run(init_db())
    return tmp_path


def test_secure_opcua_to_telemetry_chain(opcua_server, mqtt_broker, telemetry_db):
    table = load_tag_table()
    source = OpcuaSource(table, endpoint=f"opc.tcp://127.0.0.1:{opcua_server['port']}/line03/",
                         cert_dir=opcua_server["certs"], insecure=False)
    publisher = MqttPublisher("127.0.0.1", mqtt_broker, client_id="opcua-chain-collector")
    consumer = TelemetryConsumer(DbSink(), line="line03")

    async def scenario() -> None:
        from tests.integration.test_edge_e2e import _subscribe
        client = await _subscribe(consumer, "127.0.0.1", mqtt_broker)
        try:
            await asyncio.sleep(0.5)                # 等 SUBACK 生效，避免首拍消息竞态丢失
            collector = Collector(source, publisher, table)
            await collector.run(max_cycles=6)
            await asyncio.sleep(consumer.flush_interval + 0.5)
            await consumer.flush()
        finally:
            publisher.close()
            client.loop_stop()
            client.disconnect()

    asyncio.run(asyncio.wait_for(scenario(), timeout=90))

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
    # 验收要点（而非逐行数）：4 台设备都有落库 + 特征值在合理物理范围
    # （读速高于仿真源刷新率时，冻结去重会合法吞掉部分行——真实 PLC 信号
    #  逐扫描周期变化，不会触发；此处不以行数为主要断言）
    assert len(counts) == len(table.devices) and all(n >= 2 for n in counts.values()), (
        f"链路数据不足: written={consumer.written} received={consumer.received} "
        f"skipped_frozen={consumer.skipped_frozen} per_device={counts}")

    async def sample() -> dict:
        async with get_session_factory()() as db:
            row = (await db.execute(select(DeviceTelemetry).where(
                DeviceTelemetry.device_id == "line03-weld-laser-01")
                .order_by(DeviceTelemetry.cycle.desc()).limit(1))).scalar_one()
            return dict(row.readings or {})

    readings = asyncio.run(sample())
    power = float(readings["laser_power_setpoint_w"])
    assert 3000 <= power <= 4500, f"激光功率越界: {power}"
    from sqlalchemy import func, select

    from src.core.database import get_session_factory
    from src.data.models import DeviceTelemetry

    async def count() -> int:
        async with get_session_factory()() as db:
            return (await db.execute(select(func.count()).select_from(DeviceTelemetry))).scalar()

    # 总行数下限（冻结去重会合法吞掉部分行，原因见上文）
    assert asyncio.run(count()) >= 3 * len(table.devices)
