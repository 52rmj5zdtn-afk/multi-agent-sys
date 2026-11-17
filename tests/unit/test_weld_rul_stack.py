# -*- coding: utf-8 -*-
"""焊接 RUL 仿真-数据集-模型栈测试（纯 CPU，不启服务、不依赖数据库）。"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.weld_rul.dataset import Scaler, WINDOW_SIZE, build_windows, feature_cols_of, split_units
from services.weld_rul.model import LSTMRUL, load_checkpoint, predict_rul, save_checkpoint
from services.weld_rul.simulation import DEVICE_IDS, FAILURE_NOTES, simulate_fleet, simulate_unit


def _profile():
    sys.path.insert(0, str(ROOT / "scripts"))
    from seed_weld_line import _TELEMETRY_PROFILES
    return _TELEMETRY_PROFILES


def test_simulate_unit_full_life():
    rng_rows = simulate_unit("line03-weld-galvo-01", __import__("random").Random(7))
    assert 180 <= len(rng_rows) <= 560
    assert rng_rows[0]["cycle"] == 1 and rng_rows[-1]["cycle"] == len(rng_rows)
    # 末循环到达失效线（drift=1 → base+amp）
    last = rng_rows[-1]
    assert last["galvo_offset_x_um"] > 8.0     # 2 + 10*1 附近（含个体差异）


def test_simulation_feature_names_match_seed_profiles():
    profiles = _profile()
    for dev in DEVICE_IDS:
        rows = simulate_unit(dev, __import__("random").Random(11))
        feats = set(rows[0]) - {"cycle"}
        expected = set(profiles[dev])
        assert feats == expected, dev


def test_failure_notes_cover_all_devices():
    assert set(FAILURE_NOTES) == set(DEVICE_IDS)


def test_windows_and_scaler_roundtrip():
    profiles = _profile()
    fleet = simulate_fleet("line03-weld-laser-01", 4, seed=3)
    cols = feature_cols_of("line03-weld-laser-01", profiles)
    scaler = Scaler(cols).fit([r for u in fleet for r in u])
    x, y = build_windows(fleet[0], scaler)
    n = len(fleet[0]) - WINDOW_SIZE + 1
    assert x.shape == (n, WINDOW_SIZE, len(cols))
    assert y.shape == (n,)
    assert np.all(np.diff(y) <= 1e-6)          # RUL 单调下降（log 空间）
    state = scaler.state_dict()
    scaler2 = Scaler.from_state_dict(state)
    assert scaler2.cols == scaler.cols and scaler2.mean == scaler.mean


def test_model_checkpoint_roundtrip(tmp_path):
    profiles = _profile()
    fleet = simulate_fleet("line03-weld-cooling-01", 2, seed=5)
    cols = feature_cols_of("line03-weld-cooling-01", profiles)
    scaler = Scaler(cols).fit([r for u in fleet for r in u])
    model = LSTMRUL(n_features=len(cols))
    save_checkpoint(tmp_path, model, scaler, meta={"device_id": "line03-weld-cooling-01",
                                                    "model_used": "lstm-weld", "window_size": WINDOW_SIZE})
    m2, s2, meta = load_checkpoint(tmp_path)
    assert meta["device_id"] == "line03-weld-cooling-01"
    rows = fleet[0][-40:]
    out = predict_rul(m2, s2, meta, rows)
    assert set(out) >= {"rul_cycles", "padded", "window_size", "n_readings_used", "n_features"}
    assert out["padded"] is False and out["rul_cycles"] >= 0
    short = predict_rul(m2, s2, meta, rows[-5:])
    assert short["padded"] is True


def test_split_units_deterministic():
    a, b = split_units(10, seed=9)
    a2, b2 = split_units(10, seed=9)
    assert a == a2 and b == b2 and sorted(a + b) == list(range(10))
