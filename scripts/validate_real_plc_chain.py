"""真实 PLC 全链路验证：S7-1500(PLCSIM Advanced, 192.168.1.6:4840)
→ OpcuaSource(edge_collector 生产代码) → amqtt broker → DbSink(telemetry_consumer) → SQLite 断言。

用法: python scripts/validate_real_plc_chain.py [--endpoint opc.tcp://192.168.1.6:4840] [--cycles 20]
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ENDPOINT = "opc.tcp://192.168.1.6:4840"
CYCLES = 20


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def main() -> None:
    cycles = CYCLES
    if "--cycles" in sys.argv:
        cycles = int(sys.argv[sys.argv.index("--cycles") + 1])
    endpoint = ENDPOINT
    if "--endpoint" in sys.argv:
        endpoint = sys.argv[sys.argv.index("--endpoint") + 1]

    from services.edge_collector.collector import Collector, MqttPublisher
    from services.edge_collector.opcua_source import OpcuaSource
    from services.telemetry_consumer.consumer import DbSink, TelemetryConsumer

    db_path = ROOT / "deployment" / "real_plc_chain.db"
    if db_path.exists():
        db_path.unlink()

    loop = asyncio.get_running_loop()

    # --- amqtt broker ---
    from amqtt.broker import Broker
    port = _free_port()
    broker = Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}",
                                               "max_connections": -1}}, "sys_interval": 0})
    await broker.start()

    # --- consumer(DbSink) ---
    consumer = TelemetryConsumer(f"mqtt://127.0.0.1:{port}", DbSink(str(db_path)),
                                 client_id="validator")
    await consumer.start()

    # --- collector(OpcuaSource → MQTT) ---
    ns = await OpcuaSource.create(endpoint, insecure=True)
    collected = await ns.read_all()
    print(f"[plc] OPC UA 读取 {len(collected)} 个标签:")
    for t in collected[:20]:
        print(f"   {t.name} = {t.value} {t.unit or ''}")
    pub = MqttPublisher(f"mqtt://127.0.0.1:{port}", client_id="collector-v")
    await pub.start()
    collector = Collector(ns, pub)

    task = asyncio.create_task(collector.run(cycles=cycles, interval_s=0.2))
    await task
    await pub.stop()
    await asyncio.sleep(1.5)          # 给 consumer 处理时间
    await consumer.stop()
    await broker.shutdown()

    # --- 断言落库 ---
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name, COUNT(*), MAX(ts) FROM device_telemetry GROUP BY name").fetchall()
    conn.close()
    print(f"\n[db] device_telemetry 行数统计 ({len(rows)} 个标签):")
    ok = True
    for name, cnt, ts in rows:
        print(f"   {name}: {cnt} 行, 最新 ts={ts}")
        if cnt < cycles * 0.5:
            ok = False
    assert rows, "数据库中没有遥测数据 — 链路失败"
    assert ok, "部分标签数据量不足"
    print("\n✅ 全链路验证通过：真实 PLC(OPC UA) → 采集器 → MQTT → 时序库")


if __name__ == "__main__":
    asyncio.run(main())
