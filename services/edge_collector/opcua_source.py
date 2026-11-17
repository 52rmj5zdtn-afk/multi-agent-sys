"""opcua_source.py — S7-1500 OPC UA 采集源（生产模式）
=====================================================
M1 采用周期批量读（client.read_values）：1Hz × ~30 节点对 1516 CPU 负担
可忽略，且行为确定、易测试。M2 再升级为订阅模式（服务端死区 + 异步通知）。

    source = OpcuaSource(tag_table)
    await source.connect()
    snapshot = await source.read_snapshot()   # {device_id: {feature: value, "cycle": n}}
"""
from __future__ import annotations

import itertools
from pathlib import Path

from asyncua import Client

from services.edge_collector.tags import TagTable


class OpcuaSource:
    def __init__(self, tag_table: TagTable, endpoint: str | None = None,
                 namespace: int | None = None, user: str | None = None,
                 password: str | None = None, timeout: float = 10.0,
                 cert_dir: str | Path | None = None, insecure: bool = False):
        self.table = tag_table
        self.endpoint = endpoint or tag_table.opcua_endpoint
        self.namespace = namespace or tag_table.opcua_namespace
        self.user = user
        self.password = password
        self.timeout = timeout
        self.cert_dir = Path(cert_dir) if cert_dir else Path(__file__).parent / "certs"
        self.insecure = insecure          # 真机调试期可显式明文（生产禁止）
        self._client: Client | None = None
        self._cycle_fallback = itertools.count(1)   # PLC 无 cycle_tag 时的本地单调序号

    async def connect(self) -> None:
        self._client = Client(self.endpoint, timeout=self.timeout)
        if self.user:
            self._client.set_user(self.user)
            if self.password:
                self._client.set_password(self.password)
        cert, key, server_cert = (self.cert_dir / "client_cert.der",
                                  self.cert_dir / "client_key.pem",
                                  self.cert_dir / "server_cert.der")
        if not self.insecure and cert.exists() and key.exists() and server_cert.exists():
            # 证书互信: 客户端证书自证 + 显式信任 PLC/仿真服务器证书（Basic256Sha256）
            # asyncua 2.x 格式: Policy,Mode,certificate,private_key[,server_certificate]
            await self._client.set_security_string(
                f"Basic256Sha256,SignAndEncrypt,{cert},{key},{server_cert}")
        elif not self.insecure:
            import logging
            logging.getLogger(__name__).warning(
                "未找到完整证书三件套，回退无加密连接（仅限仿真/调试；"
                "生产请运行 python -m services.edge_collector.certs 生成并在 TIA 互换）")
        await self._client.connect()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    def _nodeid(self, symbol: str) -> str:
        return f"ns={self.namespace};s={symbol}"

    async def read_snapshot(self) -> dict[str, dict[str, float]]:
        if self._client is None:
            await self.connect()
        snapshot: dict[str, dict[str, float]] = {}
        for device_id, dev in self.table.devices.items():
            nodes = [self._client.get_node(self._nodeid(f.node)) for f in dev.features]
            raw = await self._client.read_values(nodes)     # 批量读，保持顺序
            values = {f.name: (float(v) if v is not None else 0.0)
                      for f, v in zip(dev.features, raw)}
            if dev.cycle_tag:
                cyc = await self._client.get_node(self._nodeid(dev.cycle_tag)).read_value()
                values["cycle"] = int(cyc) if cyc else next(self._cycle_fallback)
            else:
                values["cycle"] = next(self._cycle_fallback)
            snapshot[device_id] = values
        return snapshot
