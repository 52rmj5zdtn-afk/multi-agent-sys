"""dataset.py — 滑窗数据集构建与标准化
=====================================
把 run-to-failure 序列切成 (窗口, RUL) 样本：
  X: (N, window, n_features)  最后 window 个循环的多传感器值（标准化后）
  y: log1p(RUL)  回归目标（对数压缩长尾，小 RUL 区段梯度更大，预警更敏感）

划分按"台"切（同一台设备的窗口不跨 train/val），防止泄漏。
"""
from __future__ import annotations

import numpy as np

WINDOW_SIZE = 30


def feature_cols_of(device_id: str, profiles: dict) -> list[str]:
    """设备特征列（与 TIA 信号同名；weld_count 这类累计计数不做特征）。"""
    return [f for f in profiles[device_id] if f != "weld_count"]


def split_units(n_units: int, train_ratio: float = 0.8, seed: int = 42) -> tuple[list[int], list[int]]:
    idx = list(range(n_units))
    rng = np.random.default_rng(seed)
    rng.shuffle(idx)
    cut = int(n_units * train_ratio)
    return sorted(idx[:cut]), sorted(idx[cut:])


class Scaler:
    """逐特征标准化（train 集拟合，手写避免 sklearn 依赖进 sidecar）。"""

    def __init__(self, cols: list[str]):
        self.cols = cols
        self.mean: dict[str, float] = {}
        self.std: dict[str, float] = {}

    def fit(self, rows: list[dict]) -> "Scaler":
        for c in self.cols:
            vals = [r[c] for r in rows]
            mu = float(np.mean(vals))
            sd = float(np.std(vals)) or 1.0
            self.mean[c] = mu
            self.std[c] = sd
        return self

    def transform_row(self, row: dict) -> list[float]:
        return [((row.get(c) if row.get(c) is not None else self.mean[c]) - self.mean[c]) / self.std[c]
                for c in self.cols]

    def state_dict(self) -> dict:
        return {"cols": self.cols, "mean": self.mean, "std": self.std}

    @classmethod
    def from_state_dict(cls, d: dict) -> "Scaler":
        s = cls(list(d["cols"]))
        s.mean = {k: float(v) for k, v in d["mean"].items()}
        s.std = {k: float(v) for k, v in d["std"].items()}
        return s


def build_windows(unit_rows: list[dict], scaler: Scaler, window: int = WINDOW_SIZE,
                  feature_cols: list[str] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """单台设备序列 → 滑窗样本。RUL = 剩余循环数，标签取 log1p。"""
    cols = feature_cols or scaler.cols
    n = len(unit_rows)
    feats = np.array([scaler.transform_row(r) for r in unit_rows], dtype=np.float32)
    xs, ys = [], []
    for end in range(window, n + 1):
        xs.append(feats[end - window:end])
        rul = n - end
        ys.append(np.log1p(rul))
    if not xs:
        return np.empty((0, window, len(cols)), dtype=np.float32), np.empty((0,), dtype=np.float32)
    return np.stack(xs), np.array(ys, dtype=np.float32)
