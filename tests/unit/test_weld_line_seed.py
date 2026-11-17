# -*- coding: utf-8 -*-
"""焊接工位种子数据测试（python tests/unit/test_weld_line_seed.py；pytest 亦可收集）。

验证：遥测生成器的形状/可变性/劣化趋势，设备台账与换型映射的配置完整性。
不依赖数据库（生成器为纯函数）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from seed_weld_line import (  # noqa: E402
    WELD_DEVICES,
    WELD_INSPECTION_PRODUCTS,
    _TELEMETRY_PROFILES,
    generate_weld_telemetry,
)


def test_telemetry_shape_matches_profile():
    for dev in WELD_DEVICES:
        device_id = dev["device_id"]
        rows = generate_weld_telemetry(device_id, cycles=120, seed=7)
        assert len(rows) == 120
        assert [r["cycle"] for r in rows] == list(range(1, 121))
        expected_feats = set(_TELEMETRY_PROFILES[device_id])
        for r in rows:
            assert expected_feats <= set(r)


def test_telemetry_not_stuck():
    """每一特征列在批次内必须有变化（模拟摄入路由的卡死传感器校验）。"""
    for dev in WELD_DEVICES:
        rows = generate_weld_telemetry(dev["device_id"], cycles=120, seed=7)
        feats = set(rows[0]) - {"cycle"}
        for f in feats:
            assert len({r[f] for r in rows}) > 1, f"{dev['device_id']}.{f} 整批恒定"


def test_degradation_direction():
    """劣化方向正确：galvo 偏移上升、激光功率下降、冷却水流量下降。"""
    galvo = generate_weld_telemetry("line03-weld-galvo-01", cycles=300, seed=7)
    head = [r["galvo_offset_x_um"] for r in galvo[:30]]
    tail = [r["galvo_offset_x_um"] for r in galvo[-30:]]
    assert sum(tail) / len(tail) > sum(head) / len(head) + 3.0

    laser = generate_weld_telemetry("line03-weld-laser-01", cycles=300, seed=7)
    power_head = [r["laser_power_actual_w"] for r in laser[:30]]
    power_tail = [r["laser_power_actual_w"] for r in laser[-30:]]
    assert sum(power_tail) / len(power_tail) < sum(power_head) / len(power_head) - 30.0

    cooling = generate_weld_telemetry("line03-weld-cooling-01", cycles=300, seed=7)
    flow_head = [r["water_flow_l_min"] for r in cooling[:30]]
    flow_tail = [r["water_flow_l_min"] for r in cooling[-30:]]
    assert sum(flow_tail) / len(flow_tail) < sum(flow_head) / len(flow_head) - 1.0


def test_degradation_is_gradual():
    """拐点前（前 60%）基本健康：功率实际值维持在设定值附近。"""
    laser = generate_weld_telemetry("line03-weld-laser-01", cycles=300, seed=7)
    early = laser[: int(300 * 0.5)]
    for r in early:
        assert r["laser_power_actual_w"] >= r["laser_power_setpoint_w"] * 0.97


def test_device_registry_sanity():
    ids = [d["device_id"] for d in WELD_DEVICES]
    assert len(ids) == len(set(ids)) == 4
    for d in WELD_DEVICES:
        assert d["critical_component"] and d["rul_warn_cycles"] > 0
        assert d["cycles_per_day"] > 0
        assert "OpMode7" in d["line"]


def test_weld_inspection_products():
    codes = {p["product_code"]: p for p in WELD_INSPECTION_PRODUCTS}
    assert set(codes) == {"weld-seam-2d", "weld-seam-3d"}
    for p in codes.values():
        assert p["station_id"] == "OPMODE07-EM010"
        assert p["model_name"] in ("patchcore", "efficientad", "padim")


def test_generator_rejects_unknown_device():
    try:
        generate_weld_telemetry("no-such-device")
    except ValueError:
        return
    raise AssertionError("未登记设备应抛 ValueError")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("\nALL WELD SEED TESTS PASSED")
