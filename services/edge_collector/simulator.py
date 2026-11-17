"""simulator.py — PLC 信号源仿真器（无实体 PLC 时验证全链路）
==============================================================
按标签表中的设备，用 scripts/seed_weld_line 的退化曲线生成器
（与 RUL 训练/种子数据同一来源）逐拍步进，产出与 OpcuaSource
完全相同的快照结构 {device_id: {feature: value, "cycle": n}}。

    source = SimulatedSource(tag_table, cycles=300, hz=1.0)
    snapshot = await source.read_snapshot()   # 每调用前进一拍
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from seed_weld_line import generate_weld_telemetry  # noqa: E402

from services.edge_collector.tags import TagTable  # noqa: E402


class SimulatedSource:
    """时步推进的仿真 PLC 源：每台设备一份全寿命遥测，逐拍吐出。"""

    def __init__(self, tag_table: TagTable, cycles: int = 300, hz: float = 1.0, seed: int = 42):
        self.table = tag_table
        self.hz = hz
        self._rows: dict[str, list[dict]] = {}
        self._idx: dict[str, int] = {}
        for device_id, dev in tag_table.devices.items():
            rows = generate_weld_telemetry(device_id, cycles=cycles, seed=seed)
            feats = set(dev.feature_names)
            self._rows[device_id] = [{k: v for k, v in r.items() if k == "cycle" or k in feats}
                                     for r in rows]
            self._idx[device_id] = 0

    async def read_snapshot(self) -> dict[str, dict[str, float]]:
        """每台设备当前拍的特征值 + cycle；到达寿命末段后停在最后一拍。"""
        await asyncio.sleep(1.0 / self.hz)
        snapshot: dict[str, dict[str, float]] = {}
        for device_id, rows in self._rows.items():
            i = min(self._idx[device_id], len(rows) - 1)
            row = dict(rows[i])
            row["cycle"] = self._idx[device_id] + 1     # cycle 单调递增（含停留段）
            snapshot[device_id] = row
            self._idx[device_id] += 1
        return snapshot

    def cycles_emitted(self) -> int:
        return max(self._idx.values())
