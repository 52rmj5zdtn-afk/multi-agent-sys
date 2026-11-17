"""app.py — 边缘采集器入口
==========================
生产（接 S7-1500）:
    python -m services.edge_collector.app \
        --config services/edge_collector/tags_weld.yaml \
        --endpoint opc.tcp://192.168.0.10:4840 \
        --mqtt-host 192.168.0.20 --user edge --password ***

仿真（无 PLC，验证全链路）:
    python -m services.edge_collector.app --simulate --max-cycles 10
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from services.edge_collector.buffer import SqliteBuffer
from services.edge_collector.collector import Collector, MqttPublisher
from services.edge_collector.tags import load_tag_table


def main() -> None:
    parser = argparse.ArgumentParser(description="巢湖Line03 焊接工位边缘采集器")
    parser.add_argument("--config", default=str(Path(__file__).parent / "tags_weld.yaml"))
    parser.add_argument("--simulate", action="store_true", help="仿真 PLC 源（无实体 PLC）")
    parser.add_argument("--endpoint", default=None, help="覆盖标签表中的 OPC UA endpoint")
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--cert-dir", default=None, help="OPC UA 证书目录（含 client_cert.der/key, server_cert.der）")
    parser.add_argument("--insecure", action="store_true", help="显式允许无加密连接（仅调试）")
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--hz", type=float, default=1.0)
    parser.add_argument("--max-cycles", type=int, default=None, help="采集拍数（默认无限）")
    parser.add_argument("--buffer", default="edge_buffer.db", help="断线缓存 SQLite 路径")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    table = load_tag_table(args.config)

    if args.simulate:
        from services.edge_collector.simulator import SimulatedSource
        source = SimulatedSource(table, hz=args.hz)
    else:
        from services.edge_collector.opcua_source import OpcuaSource
        source = OpcuaSource(table, endpoint=args.endpoint, user=args.user, password=args.password,
                             cert_dir=args.cert_dir, insecure=args.insecure)

    publisher = MqttPublisher(args.mqtt_host, args.mqtt_port)
    collector = Collector(source, publisher, table, buffer=SqliteBuffer(args.buffer))
    try:
        n = asyncio.run(collector.run(max_cycles=args.max_cycles))
        logging.getLogger("edge_collector").info(f"采集结束：成功发布 {n} 条消息")
    finally:
        publisher.close()


if __name__ == "__main__":
    main()
