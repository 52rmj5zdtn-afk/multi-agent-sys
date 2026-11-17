# -*- coding: utf-8 -*-
"""sidecar HTTP 契约测试（FastAPI TestClient，不占端口）。

- RUL sidecar: 用微型 checkpoint（随机初始化 + 真实 scaler）验证
  /health、/predict 正常路径、padded 标记、未知设备 404。
- 视觉 sidecar: 桩掉 WeldInspector（不依赖训练产物）验证 multipart 上传、
  阈值覆盖、热力图落盘、3D 通道几何测量字段。
"""
import importlib
import io
import json
import random
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.weld_rul.dataset import Scaler, WINDOW_SIZE, feature_cols_of  # noqa: E402
from services.weld_rul.model import LSTMRUL, save_checkpoint  # noqa: E2
from services.weld_rul.simulation import simulate_unit  # noqa: E402


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def rul_client(tmp_path, monkeypatch):
    """临时模型目录 + 微型 checkpoint，返回 (TestClient, device_id)。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    from seed_weld_line import _TELEMETRY_PROFILES

    device_id = "line03-weld-galvo-01"
    rows = simulate_unit(device_id, random.Random(17))
    cols = feature_cols_of(device_id, _TELEMETRY_PROFILES)
    scaler = Scaler(cols).fit(rows)
    save_checkpoint(tmp_path / device_id, LSTMRUL(n_features=len(cols)), scaler,
                    meta={"device_id": device_id, "model_used": "lstm-weld",
                          "window_size": WINDOW_SIZE})

    monkeypatch.setenv("WELD_RUL_MODEL_DIR", str(tmp_path))
    app_mod = importlib.reload(importlib.import_module("services.weld_rul.app"))
    from fastapi.testclient import TestClient
    with TestClient(app_mod.app) as client:
        yield client, device_id, rows


def test_rul_health_and_predict(rul_client):
    client, device_id, rows = rul_client
    health = client.get("/health").json()
    assert health["status"] == "ok" and device_id in health["devices"]

    resp = client.post("/predict", json={"device_id": device_id, "readings": rows[-40:]})
    assert resp.status_code == 200
    body = resp.json()
    for key in ("rul_cycles", "padded", "window_size", "n_readings_used", "n_features", "model_used"):
        assert key in body, key
    assert body["padded"] is False and body["n_features"] > 0 and body["rul_cycles"] >= 0


def test_rul_padded_short_readings(rul_client):
    client, device_id, rows = rul_client
    body = client.post("/predict", json={"device_id": device_id, "readings": rows[-5:]}).json()
    assert body["padded"] is True


def test_rul_unknown_device_404(rul_client):
    client, _, rows = rul_client
    resp = client.post("/predict", json={"device_id": "no-such", "readings": rows[-40:]})
    assert resp.status_code == 404


def test_rul_empty_readings_422(rul_client):
    client, device_id, _ = rul_client
    assert client.post("/predict", json={"device_id": device_id, "readings": []}).status_code == 422


class _StubInspector:
    """返回固定分数的桩；depth 通道走真实几何测量。"""

    def inspect(self, image, model_name, tag="2d", threshold=None, heatmap_path=None):
        from services.weld_vision.synth_weld import geometry_verdict, gray_to_depth, measure_bead
        thr = float(threshold if threshold is not None else 0.5)
        score = 0.9
        measurements = None
        violations = []
        defect = score >= thr
        if tag == "depth":
            measurements = measure_bead(gray_to_depth(np.array(image.convert("L"), dtype=np.float32)))
            geo_ok, violations = geometry_verdict(measurements)
            defect = defect or not geo_ok
        if heatmap_path is not None:
            Image.fromarray(np.zeros((64, 64), dtype=np.uint8)).save(str(heatmap_path))
        return {"defect_detected": defect, "anomaly_score": score, "threshold": thr,
                "anomaly_map_path": str(heatmap_path or ""), "model": model_name, "tag": tag,
                "inferred_at": "stub", "measurements": measurements,
                "geometry_violations": violations}


@pytest.fixture()
def vision_client(tmp_path, monkeypatch):
    monkeypatch.setenv("WELD_VISION_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("WELD_VISION_HEATMAP_DIR", str(tmp_path / "heatmaps"))
    app_mod = importlib.reload(importlib.import_module("services.weld_vision.app"))
    from fastapi.testclient import TestClient
    with TestClient(app_mod.app) as client:
        app_mod._inspector = _StubInspector()
        yield client, tmp_path


def test_vision_inspect_contract(vision_client):
    client, tmp_path = vision_client
    from services.weld_vision.synth_weld import render_weld_image
    img, _, _ = render_weld_image(random.Random(5), False)
    resp = client.post("/inspect?model=patchcore&tag=2d",
                       files={"file": ("weld.png", _png_bytes(img), "image/png")})
    assert resp.status_code == 200
    body = resp.json()
    for key in ("defect_detected", "anomaly_score", "threshold", "anomaly_map_path", "model", "inferred_at", "image"):
        assert key in body, key
    assert body["defect_detected"] is True            # 0.9 >= 默认阈值 0.5
    assert body["anomaly_map_path"] and Path(body["anomaly_map_path"]).exists()


def test_vision_threshold_override(vision_client):
    client, _ = vision_client
    from services.weld_vision.synth_weld import render_weld_image
    img, _, _ = render_weld_image(random.Random(6), False)
    body = client.post("/inspect?model=patchcore&tag=2d&threshold=0.95",
                       files={"file": ("weld.png", _png_bytes(img), "image/png")}).json()
    assert body["threshold"] == 0.95 and body["defect_detected"] is False


def test_vision_depth_measurements(vision_client):
    client, _ = vision_client
    from services.weld_vision.synth_weld import render_depth_image, render_weld_image
    _, depth, _ = render_weld_image(random.Random(7), False)
    body = client.post("/inspect?model=patchcore&tag=depth",
                       files={"file": ("depth.png", _png_bytes(render_depth_image(depth)), "image/png")}).json()
    assert body["tag"] == "depth"
    assert body["measurements"] and "bead_width_mm" in body["measurements"]
    assert body["geometry_violations"] == []          # 正常品无超窗


def test_vision_depth_flags_geometry_defect(vision_client):
    client, _ = vision_client
    from services.weld_vision.synth_weld import render_depth_image, render_weld_image
    _, depth, _ = render_weld_image(random.Random(8), True, "lack_of_fusion")
    body = client.post("/inspect?model=patchcore&tag=depth&threshold=0.99",
                       files={"file": ("depth.png", _png_bytes(render_depth_image(depth)), "image/png")}).json()
    assert body["defect_detected"] is True
    assert body["geometry_violations"]
