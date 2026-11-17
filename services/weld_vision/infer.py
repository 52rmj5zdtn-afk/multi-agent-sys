"""infer.py — 焊缝检测推理封装
==============================
加载 anomalib torch 导出模型，对单张图片输出:
  anomaly_score  异常分数（anomaly map 均值）
  anomaly_map    像素级热力图（保存 PNG，供 inspection_records.heatmap_path 追溯）
  measurements   3D 通道的几何测量（熔宽/余高/咬边，由深度图灰度直接计算）

2D 外观缺陷靠异常分数；3D 形貌异常 = 异常分数 OR 几何量超窗（双判据）。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

# anomalib 2.7 安全开关: 加载 torch 检查点需显式信任（本服务只加载自训模型）
os.environ.setdefault("TRUST_REMOTE_CODE", "1")

import numpy as np
import torch
from anomalib.deploy import TorchInferencer
from PIL import Image

from services.weld_vision.synth_weld import geometry_verdict, measure_bead


class WeldInspector:
    """按 (model_name, tag) 缓存 TorchInferencer。"""

    def __init__(self, model_root: str | Path):
        self.model_root = Path(model_root)
        self._inferencers: dict[str, TorchInferencer] = {}
        self._metas: dict[str, dict] = {}

    def _key(self, model_name: str, tag: str) -> str:
        return f"{model_name}-{tag}"

    def _load(self, model_name: str, tag: str) -> tuple[TorchInferencer, dict]:
        key = self._key(model_name, tag)
        if key not in self._inferencers:
            # anomalib Engine.export(TORCH) 的产物布局: weights/torch/<name>.pt
            ckpt = self.model_root / "weights" / "torch" / f"{key}.pt"
            if not ckpt.exists():                       # 兼容直接放在根目录的旧布局
                ckpt = self.model_root / f"{key}.pt"
            meta_file = self.model_root / f"{key}.json"
            if not ckpt.exists():
                raise FileNotFoundError(f"模型不存在: {ckpt}")
            self._inferencers[key] = TorchInferencer(path=str(ckpt), device="cpu")
            self._metas[key] = (
                __import__("json").loads(meta_file.read_text(encoding="utf-8"))
                if meta_file.exists() else {}
            )
        return self._inferencers[key], self._metas[key]

    def inspect(self, image: Image.Image, model_name: str, tag: str = "2d",
                threshold: float | None = None, heatmap_path: str | Path | None = None) -> dict:
        inferencer, meta = self._load(model_name, tag)
        result = inferencer.predict(np.array(image))
        anomaly_map = result.anomaly_map
        if isinstance(anomaly_map, torch.Tensor):
            anomaly_map = anomaly_map.cpu().numpy()
        score = float(np.mean(anomaly_map))
        thr = float(threshold if threshold is not None else meta.get("threshold", 0.5))

        defect = score >= thr
        measurements = None
        violations: list[str] = []
        if tag == "depth":
            from services.weld_vision.synth_weld import gray_to_depth
            gray = gray_to_depth(np.array(image.convert("L"), dtype=np.float32))
            measurements = measure_bead(gray)
            geo_ok, violations = geometry_verdict(measurements)
            defect = defect or not geo_ok

        map_path = ""
        if heatmap_path is not None:
            map_path = str(heatmap_path)
            amap = anomaly_map
            if amap.ndim == 3:
                amap = amap[0]
            amap = (amap - amap.min()) / max(1e-8, (amap.max() - amap.min()))
            Image.fromarray((amap * 255).astype(np.uint8), mode="L").convert("RGB").save(map_path)

        return {
            "defect_detected": bool(defect),
            "anomaly_score": round(score, 4),
            "threshold": thr,
            "anomaly_map_path": map_path,
            "model": model_name,
            "tag": tag,
            "inferred_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "measurements": measurements,
            "geometry_violations": violations,
        }
