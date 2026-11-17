"""app.py — RUL 推理 sidecar（:8002，契约对齐 src/ml/rul.py）
=============================================================
端点:
  GET  /health  → {status, devices}
  POST /predict {device_id, readings, model?} →
       {device_id, rul_cycles, padded, window_size, n_readings_used, n_features, model_used}

启动:  uvicorn services.weld_rul.app:app --port 8002
环境:  WELD_RUL_MODEL_DIR（默认 <repo>/models/weld_rul）
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from services.weld_rul.model import load_checkpoint, predict_rul

_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "weld_rul"
MODEL_DIR = Path(os.environ.get("WELD_RUL_MODEL_DIR", str(_DEFAULT_MODEL_DIR)))

app = FastAPI(title="weld-rul-sidecar", version="1.0.0")
_checkpoints: dict[str, tuple] = {}


def _load_all() -> None:
    _checkpoints.clear()
    if not MODEL_DIR.exists():
        return
    for device_dir in sorted(MODEL_DIR.iterdir()):
        if (device_dir / "model.pt").exists():
            _checkpoints[device_dir.name] = load_checkpoint(device_dir)


@app.on_event("startup")
def startup() -> None:
    _load_all()


class PredictRequest(BaseModel):
    device_id: str
    readings: list[dict]
    model: str = "lstm"


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model_dir": str(MODEL_DIR),
        "devices": sorted(_checkpoints),
        "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.post("/predict")
def predict(req: PredictRequest) -> dict:
    if not req.readings:
        raise HTTPException(422, "readings 为空")
    ckpt = _checkpoints.get(req.device_id)
    if ckpt is None:
        raise HTTPException(
            404,
            f"未找到设备 {req.device_id} 的 RUL 模型；已加载: {sorted(_checkpoints)}",
        )
    model, scaler, meta = ckpt
    result = predict_rul(model, scaler, meta, req.readings)
    result["device_id"] = req.device_id
    result["model_used"] = meta.get("model_used", "lstm-weld")
    return result
