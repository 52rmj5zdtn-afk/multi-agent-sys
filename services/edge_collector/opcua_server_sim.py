"""opcua_server_sim.py — S7-1500 风格 OPC UA 仿真服务器
=======================================================
用 asyncua 按 tags_weld.yaml 暴露 S7 符号 NodeId（ns=3;s="IF_HMI"...），
实时值由 SimulatedSource（与 RUL 训练同源的退化曲线）驱动——生产代码
OpcuaSource 无需任何改动即可对接；等真机/PLCSIM Advanced 就绪后只需
把 endpoint 换成 PLC 地址并互换证书。

支持 Basic256Sha256_SignAndEncrypt（自签证书，证书生成见 certs.py）：
验证"加密 + 证书互信"这条真实产线必经路径。

    python -m services.edge_collector.opcua_server_sim --port 4840 --secure
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from asyncua import Server, ua

from services.edge_collector.simulator import SimulatedSource
from services.edge_collector.tags import TagTable, load_tag_table

logger = logging.getLogger("opcua_server_sim")

APP_URI = "urn:line03:opcua-sim-server"


class SimOpcuaServer:
    def __init__(self, tag_table: TagTable, host: str = "127.0.0.1", port: int = 4840,
                 hz: float = 1.0, secure: bool = False, cert_dir: str | Path | None = None,
                 cycles: int = 300, seed: int = 42):
        self.table = tag_table
        self.host, self.port = host, port
        self.secure = secure
        self.cert_dir = Path(cert_dir) if cert_dir else Path(__file__).parent / "certs"
        self.source = SimulatedSource(tag_table, cycles=cycles, hz=hz, seed=seed)
        self._server: Server | None = None          # 延迟到 start() 创建（绑定所在线程事件循环）
        self._nodes: dict[str, dict[str, object]] = {}   # device -> feature -> node
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        # Server 必须在其运行线程的事件循环中构造（asyncua 构造时抓取当前循环）
        self._server = Server()
        await self._server.init()
        self._server.set_server_name("SIMATIC S7-1500 OPC UA Server (Line03 weld cell sim)")
        scheme = "opc.tcp"
        self._server.set_endpoint(f"{scheme}://{self.host}:{self.port}/line03/")

        if self.secure:
            cert = self.cert_dir / "server_cert.der"
            key = self.cert_dir / "server_key.pem"
            if not (cert.exists() and key.exists()):
                from services.edge_collector.certs import generate_certs
                generate_certs(self.cert_dir, server_uri=APP_URI)
            await self._server.load_certificate(str(cert))
            await self._server.load_private_key(str(key))
            self._server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])

        # 对齐 S7-1500：符号节点固定 ns=3（ns0=标准, ns1=服务器内部, 自定义从 2 起）
        await self._server.register_namespace("urn:line03:reserved")   # 占住 ns=2
        ns = await self._server.register_namespace(APP_URI)            # APP_URI → ns=3
        logger.info(f"仿真服务器命名空间: APP_URI → ns={ns}")
        objects = self._server.nodes.objects
        node_index: dict[str, object] = {}            # node 路径 → ua 节点（去重）
        for device_id, dev in self.table.devices.items():
            self._nodes[device_id] = {}
            for feat in dev.features:
                ua_node = node_index.get(feat.node)
                if ua_node is None:
                    ua_node = await objects.add_variable(
                        ua.NodeId(feat.node, ns), feat.name,
                        ua.Variant(0.0, ua.VariantType.Double))
                    await ua_node.set_writable(False)
                    node_index[feat.node] = ua_node
                self._nodes[device_id][feat.name] = ua_node   # 多特征可共享同一节点
            cycle_symbol = dev.cycle_tag or f'"SIM"."{device_id.upper()}"."CYCLE"'
            self._nodes[device_id]["cycle"] = await objects.add_variable(
                ua.NodeId(cycle_symbol, ns), f"{device_id}.cycle",
                ua.Variant(0, ua.VariantType.UInt32))
        await self._server.start()
        self._task = asyncio.create_task(self._write_loop())
        logger.info(f"OPC UA 仿真服务器已启动: {scheme}://{self.host}:{self.port} "
                    f"(secure={self.secure})")

    async def _write_loop(self) -> None:
        while True:
            snapshot = await self.source.read_snapshot()
            for device_id, values in snapshot.items():
                nodes = self._nodes.get(device_id, {})
                for feat, val in values.items():
                    node = nodes.get(feat)
                    if node is not None:
                        vtype = ua.VariantType.UInt32 if feat == "cycle" else ua.VariantType.Double
                        await node.set_value(ua.Variant(val, vtype))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._server is not None:
            await self._server.stop()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="S7-1500 风格 OPC UA 仿真服务器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4840)
    parser.add_argument("--hz", type=float, default=1.0)
    parser.add_argument("--secure", action="store_true", help="Basic256Sha256 签名加密")
    parser.add_argument("--config", default=str(Path(__file__).parent / "tags_weld.yaml"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    table = load_tag_table(args.config)
    server = SimOpcuaServer(table, host=args.host, port=args.port, hz=args.hz,
                            secure=args.secure)
    try:
        asyncio.run(server.start())
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        asyncio.run(server.stop())


if __name__ == "__main__":
    main()
