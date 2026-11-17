"""calibrate.py — 视觉检测阈值再标定
====================================
训练时阈值取自带标签测试集的正常品最大值（样本少，偏乐观）。
这里用生成器新采 n 张正常品，取分数最大值 × 裕量作为运行阈值，
写回 <model_root>/<model_name>-<tag>.json（sidecar 每次加载时读取）。

    python -m services.weld_vision.calibrate
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from services.weld_vision.infer import WeldInspector
from services.weld_vision.synth_weld import render_depth_image, render_weld_image

DEFAULT_MODEL_ROOT = Path(__file__).resolve().parents[2] / "models" / "weld_vision"


def calibrate(model_root: str | Path = DEFAULT_MODEL_ROOT, model_name: str = "patchcore",
              tags: tuple[str, ...] = ("2d", "depth"), n: int = 60, margin: float = 1.08,
              image_size: int = 128, seed: int = 49) -> dict[str, float]:
    """对每通道采样 n 张新正常品，阈值 = max(score) × margin，写回 meta json。"""
    model_root = Path(model_root)
    thresholds: dict[str, float] = {}
    for tag in tags:
        ckpt = model_root / "weights" / "torch" / f"{model_name}-{tag}.pt"
        if not ckpt.exists():
            print(f"[{tag}] 跳过：模型不存在 {ckpt}")
            continue
        insp = WeldInspector(model_root)
        rng = random.Random(seed + (0 if tag == "2d" else 1000))
        scores = []
        for _ in range(n):
            img, depth, _ = render_weld_image(rng, defective=False)
            if tag == "depth":
                img = render_depth_image(depth)
            # 不预缩放: 导出的 torch 模型自带前处理，且 256px 保证几何测量标定有效
            r = insp.inspect(img, model_name=model_name, tag=tag, threshold=1e9)
            scores.append(r["anomaly_score"])
        thr = round(max(scores) * margin, 4)
        meta_path = model_root / f"{model_name}-{tag}.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        meta["threshold"] = thr
        meta["threshold_source"] = f"calibrate: {n} fresh normals × {margin} margin (max={max(scores):.4f})"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        thresholds[tag] = thr
        print(f"[{tag}] 阈值 {thr}（{n} 张正常品最大分数 {max(scores):.4f} × {margin}）")
    return thresholds


if __name__ == "__main__":
    calibrate()
