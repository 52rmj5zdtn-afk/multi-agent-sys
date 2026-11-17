"""model.py — LSTM RUL 回归模型与 checkpoint 存取
===============================================
小模型（2 层 LSTM hidden=64），CPU 毫秒级推理；契约与 src/ml/rul.py 客户端对齐：
predict_rul(readings) → (rul_cycles, padded, n_readings_used, n_features)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

from services.weld_rul.dataset import Scaler

RUL_MAX_CYCLES = 5000


class LSTMRUL(nn.Module):
    """输入标准化后的 (B, window, n_features)，输出 log1p(RUL) 预测。"""

    def __init__(self, n_features: int, hidden: int = 64, num_layers: int = 2, dropout: float = 0.2):
        super().__init__()
        self.lstm = nn.LSTM(n_features, hidden, num_layers=num_layers,
                            batch_first=True, dropout=dropout)
        self.head = nn.Sequential(nn.Linear(hidden, 32), nn.ReLU(), nn.Linear(32, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :]).squeeze(-1)


def save_checkpoint(model_dir: str | Path, model: LSTMRUL, scaler: Scaler, meta: dict) -> None:
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), model_dir / "model.pt")
    (model_dir / "scaler.json").write_text(
        json.dumps(scaler.state_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (model_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def load_checkpoint(model_dir: str | Path) -> tuple[LSTMRUL, Scaler, dict]:
    model_dir = Path(model_dir)
    meta = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
    scaler = Scaler.from_state_dict(json.loads((model_dir / "scaler.json").read_text(encoding="utf-8")))
    model = LSTMRUL(n_features=len(scaler.cols), hidden=meta.get("hidden", 64),
                    num_layers=meta.get("num_layers", 2))
    model.load_state_dict(torch.load(model_dir / "model.pt", map_location="cpu", weights_only=True))
    model.eval()
    return model, scaler, meta


@torch.no_grad()
def predict_rul(model: LSTMRUL, scaler: Scaler, meta: dict, readings: list[dict]) -> dict:
    """对原始量纲 readings（时间升序）预测 RUL。窗口不足时边缘补齐并标记 padded。"""
    window = int(meta.get("window_size", 30))
    rows = [{c: r.get(c) for c in scaler.cols} for r in readings]
    padded = len(rows) < window
    if padded:
        pad = [rows[0]] * (window - len(rows))
        rows = pad + rows
    feats = np.array([scaler.transform_row(r) for r in rows[-window:]], dtype=np.float32)
    x = torch.from_numpy(feats).unsqueeze(0)
    pred = float(model(x).item())
    rul = int(round(np.expm1(pred)))
    rul = max(0, min(RUL_MAX_CYCLES, rul))
    return {
        "rul_cycles": rul,
        "padded": padded,
        "window_size": window,
        "n_readings_used": len(readings),
        "n_features": len(scaler.cols),
        "model_used": meta.get("model_used", "lstm-weld"),
    }
