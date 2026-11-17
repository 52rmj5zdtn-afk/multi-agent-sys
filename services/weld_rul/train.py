"""train.py — 焊接工位 RUL 模型训练入口
======================================
对 4 台焊接工位设备分别训练 LSTM RUL 模型：

    python -m services.weld_rul.train --units 240 --epochs 30

产物（models/weld_rul/<device_id>/）:
  model.pt / scaler.json / meta.json / eval_metrics.json

评估指标: 测试机群 RUL 的 MAE（循环数），并在真实劣化阈值处报告命中率——
预警类模型关心"低 RUL 区段误差"，不关心全区间平均。
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from services.weld_rul.dataset import Scaler, WINDOW_SIZE, build_windows, feature_cols_of, split_units
from services.weld_rul.model import LSTMRUL, save_checkpoint
from services.weld_rul.simulation import DEVICE_IDS, FAILURE_NOTES, simulate_fleet

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "models" / "weld_rul"


def _to_loader(xs: np.ndarray, ys: np.ndarray, batch: int, shuffle: bool) -> DataLoader:
    ds = TensorDataset(torch.from_numpy(xs), torch.from_numpy(ys))
    return DataLoader(ds, batch_size=batch, shuffle=shuffle)


def train_device(device_id: str, units: int, epochs: int, out_root: Path,
                 batch: int = 64, lr: float = 1e-3, patience: int = 6, seed: int = 42) -> dict:
    from seed_weld_line import _TELEMETRY_PROFILES

    cols = feature_cols_of(device_id, _TELEMETRY_PROFILES)
    fleet = simulate_fleet(device_id, units, seed=seed)
    train_idx, val_idx = split_units(units, train_ratio=0.8, seed=seed)

    scaler = Scaler(cols)
    scaler.fit([r for i in train_idx for r in fleet[i]])

    def stack(indices):
        xs, ys = [], []
        for i in indices:
            x, y = build_windows(fleet[i], scaler, window=WINDOW_SIZE, feature_cols=cols)
            if len(x):
                xs.append(x)
                ys.append(y)
        return np.concatenate(xs), np.concatenate(ys)

    x_tr, y_tr = stack(train_idx)
    x_va, y_va = stack(val_idx)
    train_loader = _to_loader(x_tr, y_tr, batch, shuffle=True)
    val_x, val_y = torch.from_numpy(x_va), torch.from_numpy(y_va)

    model = LSTMRUL(n_features=len(cols))
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    best_val, best_state, bad = float("inf"), None, 0
    for epoch in range(1, epochs + 1):
        model.train()
        t0 = time.time()
        total = 0.0
        for xb, yb in train_loader:
            opt.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            opt.step()
            total += float(loss) * len(xb)
        model.eval()
        with torch.no_grad():
            val_loss = float(nn.functional.mse_loss(model(val_x), val_y))
        if val_loss < best_val - 1e-5:
            best_val, bad = val_loss, 0
            best_state = copy.deepcopy(model.state_dict())
        else:
            bad += 1
        print(f"  [{device_id}] epoch {epoch:02d}  train_mse={total/len(x_tr):.5f} "
              f"val_mse={val_loss:.5f} ({time.time()-t0:.1f}s)")
        if bad >= patience:
            print(f"  [{device_id}] early stop @ epoch {epoch}")
            break

    model.load_state_dict(best_state)
    model.eval()

    # 测试机群：整机寿命末段 RUL 误差（预警低 RUL 区段）
    maes, low_rul_hits, low_rul_n = [], 0, 0
    for i in val_idx:
        rows = fleet[i]
        n = len(rows)
        xs, ys = build_windows(rows, scaler, window=WINDOW_SIZE, feature_cols=cols)
        if not len(xs):
            continue
        with torch.no_grad():
            pred = model(torch.from_numpy(xs)).numpy()
        pred_rul = np.expm1(pred)
        true_rul = np.expm1(ys)
        maes.append(float(np.mean(np.abs(pred_rul - true_rul))))
        mask = true_rul <= 80          # 设备级预警阈值附近区段
        if mask.any():
            low_rul_hits += int(np.mean(np.abs(pred_rul[mask] - true_rul[mask])) <= 30)
            low_rul_n += 1
    mae = float(np.mean(maes))
    metrics = {
        "device_id": device_id,
        "test_units": len(val_idx),
        "rul_mae_cycles": round(mae, 1),
        "low_rul_mae<=30_cycles_ratio": round(low_rul_hits / max(1, low_rul_n), 3),
        "failure_criterion": FAILURE_NOTES.get(device_id, ""),
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    out_dir = out_root / device_id
    save_checkpoint(out_dir, model, scaler, meta={
        "device_id": device_id,
        "model_used": "lstm-weld",
        "window_size": WINDOW_SIZE,
        "hidden": 64,
        "num_layers": 2,
        "feature_cols": cols,
        "failure_criterion": FAILURE_NOTES.get(device_id, ""),
    })
    (out_dir / "eval_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [{device_id}] 测试 RUL MAE={mae:.1f} 循环 → {out_dir}")
    return metrics


def main():
    parser = argparse.ArgumentParser(description="训练焊接工位 RUL 模型（4 台设备）")
    parser.add_argument("--units", type=int, default=240, help="仿真机群台数（默认 240）")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="模型输出根目录")
    args = parser.parse_args()

    torch.manual_seed(42)
    out_root = Path(args.out)
    all_metrics = []
    for device_id in DEVICE_IDS:
        print(f"=== 训练 {device_id} ===")
        all_metrics.append(train_device(device_id, args.units, args.epochs, out_root))
    (out_root / "train_summary.json").write_text(
        json.dumps(all_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n全部完成，模型目录: {out_root}")


if __name__ == "__main__":
    main()
