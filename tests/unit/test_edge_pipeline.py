# -*- coding: utf-8 -*-
"""边缘采集链路单元测试：标签映射 / 断线缓存 / 仿真源 / 消费者去重。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.edge_collector.buffer import SqliteBuffer
from services.edge_collector.simulator import SimulatedSource
from services.edge_collector.tags import load_tag_table
from services.telemetry_consumer.consumer import TelemetryConsumer

TAGS = Path(__file__).resolve().parents[2] / "services" / "edge_collector" / "tags_weld.yaml"


def test_tag_table_loads_all_devices():
    table = load_tag_table(TAGS)
    assert table.line == "line03" and table.station == "opmode7-em040"
    assert set(table.devices) == {"line03-weld-laser-01"}
    laser = table.devices["line03-weld-laser-01"]
    assert laser.cycle_tag and "WeldTotalNumTimes" in laser.cycle_tag
    # 6 个特征全部来自真实 PLC 地址空间浏览确认（deployment/opcua_weld_subtrees.json）
    assert len(laser.features) == 6 and laser.feature_names[0] == "laser_power_setpoint_w"


def test_map_snapshot_filters_unknown_nodes():
    table = load_tag_table(TAGS)
    mapped = table.map_snapshot("line03-weld-laser-01", {
        '"GLOBAL_LASERWELDING_HMI_IF_DB"."LaserTRUMPF"[1]."Laser"."ActualLaserPower"': 3200.0,
        '"GLOBAL_LASERWELDING_HMI_IF_DB"."LaserTRUMPF"[1]."Laser"."Output"."EMISSION_ON"': True,
        '"Unknown"."Node"': 999.0,
    })
    assert mapped == {"laser_power_actual_w": 3200.0, "emission_on": True}


def test_buffer_roundtrip_and_persistence(tmp_path):
    db = tmp_path / "buf.db"
    buf = SqliteBuffer(db)
    buf.put("t/1", {"a": 1}, ts=100.0)
    buf.put("t/2", {"a": 2}, ts=101.0)
    assert len(buf) == 2

    buf2 = SqliteBuffer(db)                    # 新实例（模拟进程重启）
    pending = buf2.drain(10)
    assert [p[1] for p in pending] == ["t/1", "t/2"]
    assert pending[0][2] == {"a": 1} and pending[1][3] == 101.0
    buf2.ack(pending[0][0])
    assert len(buf2) == 1


def test_simulator_snapshot_matches_profiles():
    table = load_tag_table(TAGS)
    src = SimulatedSource(table, cycles=120, hz=1000, seed=7)   # 1ms 节拍加速测试
    snap = asyncio.run(src.read_snapshot())
    for device_id, dev in table.devices.items():
        assert device_id in snap
        row = snap[device_id]
        assert row["cycle"] == 1
        # 快照只包含本设备特征（仿真器按标签表过滤），核心功率特征必须有
        assert set(row) - {"cycle"} <= set(dev.feature_names)
        assert "laser_power_setpoint_w" in row and "laser_power_actual_w" in row
    snap2 = asyncio.run(src.read_snapshot())
    assert snap2["line03-weld-laser-01"]["cycle"] == 2


class _FakeSink:
    def __init__(self):
        self.batches: list[tuple[str, list[dict]]] = []

    async def write(self, device_id: str, rows: list[dict]) -> int:
        self.batches.append((device_id, rows))
        return len(rows)


def test_consumer_skips_frozen_rows_and_parses_topic():
    sink = _FakeSink()
    consumer = TelemetryConsumer(sink, line="line03")
    payload = lambda cyc, v: {"cycle": cyc, "ts": 1.0, "readings": {"galvo_offset_x_um": v}}

    consumer.on_message("line03/opmode7-em040/line03-weld-galvo-01/telemetry",
                        __import__("json").dumps(payload(1, 2.0)).encode())
    consumer.on_message("line03/opmode7-em040/line03-weld-galvo-01/telemetry",
                        __import__("json").dumps(payload(2, 2.0)).encode())   # 冻结 → 跳过
    consumer.on_message("line03/opmode7-em040/line03-weld-galvo-01/telemetry",
                        __import__("json").dumps(payload(3, 2.1)).encode())
    assert consumer.received == 2 and consumer.skipped_frozen == 1

    asyncio.run(consumer.flush())
    assert len(sink.batches) == 1
    device_id, rows = sink.batches[0]
    assert device_id == "line03-weld-galvo-01"
    assert [r["cycle"] for r in rows] == [1, 3] and rows[1]["galvo_offset_x_um"] == 2.1
