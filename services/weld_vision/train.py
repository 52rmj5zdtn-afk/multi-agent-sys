"""train.py — 焊缝异常检测模型训练（anomalib Patchcore / EfficientAd）
==============================================================
合成数据 → anomalib Folder 数据模块 → 训练 → torch 导出 + 阈值标定。

阈值标定策略: 无监督检测以"正常品最大分数 + 5% 裕量"为判定阈值，
并在带标签测试集上报告 image-level F1/召回率（信息用途，不参与训练）。

    python -m services.weld_vision.train --model patchcore
    python -m services.weld_vision.train --model efficientad --tag depth   # 3D 深度图通道
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

# huggingface.co 在部分网络不可达：timm 骨干权重走镜像（可用环境变量覆盖）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from anomalib.data import Folder
from anomalib.deploy import ExportType
from anomalib.engine import Engine
from anomalib.models import EfficientAd, Patchcore

from services.weld_vision.synth_weld import generate_dataset


def _patch_anomalib_ascii_path_guard() -> None:
    """anomalib 的路径校验只允许 ASCII 可打印字符（[\\x20-\\x7E]），
    中文工程路径会被误判为 non-printable；放宽为 Unicode 可打印字符。
    只改内存中的函数引用，不动 site-packages。"""
    from anomalib.data.utils import path as anomalib_path

    anomalib_path.contains_non_printable_characters = (
        lambda p: not all(ch.isprintable() for ch in str(p))
    )

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "weld_vision"
DEFAULT_MODEL_ROOT = Path(__file__).resolve().parents[2] / "models" / "weld_vision"


def train(model_name: str = "patchcore", tag: str = "2d", image_size: int = 128,
          data_root: Path = DEFAULT_DATA_ROOT, model_root: Path = DEFAULT_MODEL_ROOT,
          n_train: int = 120, coreset_ratio: float = 0.02, seed: int = 42) -> dict:
    stats = generate_dataset(data_root, n_train=n_train, seed=seed, out_size=image_size)
    root = Path(str(data_root) + "_depth") if tag == "depth" else data_root
    _patch_anomalib_ascii_path_guard()

    datamodule = Folder(
        name=f"weld-seam-{tag}",
        root=root,
        normal_dir="train/good",
        abnormal_dir="test/defective",
        normal_test_dir="test/good",
        train_batch_size=16,
        eval_batch_size=16,
        num_workers=0,
        seed=seed,
    )

    if model_name == "patchcore":
        # coreset 采样率 2%: k-center greedy 每轮全量扫描嵌入，CPU 上代价随
        # 补丁数×选中数线性增长（内存带宽瓶颈），默认 10% 在此环境要跑小时级
        model = Patchcore(backbone="resnet18", coreset_sampling_ratio=coreset_ratio)
    elif model_name == "padim":
        # CPU 友好: 单次前向收集嵌入统计量（均值/协方差），无核心集贪心
        from anomalib.models import Padim
        model = Padim(backbone="resnet18")
    elif model_name == "efficientad":
        model = EfficientAd()
    else:
        raise ValueError(f"不支持的模型: {model_name}")

    engine = Engine(default_root_dir=str(model_root / "_engine" / f"{model_name}-{tag}"))
    engine.fit(model=model, datamodule=datamodule)

    # 单遍测试集打分: 阈值=正常品分数最大值+5% 裕量；顺带报 image-level 指标（信息用途）
    # 注意: datamodule.test_dataloader() 是原始数据（无 pred_score），必须用 engine.predict
    batches = engine.predict(model=model, datamodule=datamodule)
    scores: list[tuple[str, float]] = []
    for batch in batches:
        for p, s in zip(batch.image_path, batch.pred_score):
            scores.append((str(p), float(s)))
    normal_scores = [s for p, s in scores if f"{os.sep}good{os.sep}" in p or "/good/" in p]
    threshold = round(max(normal_scores) * 1.05, 4) if normal_scores else 0.5
    tp = sum(1 for p, s in scores if ("good" not in p) and s >= threshold)
    fn = sum(1 for p, s in scores if ("good" not in p) and s < threshold)
    fp = sum(1 for p, s in scores if ("good" in p) and s >= threshold)
    tn = sum(1 for p, s in scores if ("good" in p) and s < threshold)
    test_metrics = {
        "recall": round(tp / max(1, tp + fn), 3),
        "precision": round(tp / max(1, tp + fp), 3),
        "accuracy": round((tp + tn) / max(1, len(scores)), 3),
        "n_scored": len(scores),
    }

    export_path = engine.export(
        model=model, export_type=ExportType.TORCH,
        export_root=str(model_root), model_file_name=f"{model_name}-{tag}",
        input_size=(image_size, image_size),
    )

    meta = {
        "model_name": model_name,
        "tag": tag,
        "threshold": threshold,
        "image_size": image_size,
        "dataset_stats": stats,
        "test_metrics": test_metrics,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    meta_path = model_root / f"{model_name}-{tag}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"模型导出: {export_path}\n元数据: {meta_path}\n阈值={threshold} "
          f"测试指标={test_metrics}")

    # 阈值再标定: 小样本测试集偏乐观，用新采正常品校准运行阈值
    from services.weld_vision.calibrate import calibrate
    calibrate(model_root=model_root, model_name=model_name, tags=(tag,), n=60, margin=1.08,
              image_size=image_size)
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="训练焊缝异常检测模型")
    parser.add_argument("--model", default="patchcore", choices=["patchcore", "efficientad", "padim"])
    parser.add_argument("--tag", default="2d", choices=["2d", "depth"], help="2d 外观 / depth 3D 深度图")
    parser.add_argument("--image-size", type=int, default=128)
    parser.add_argument("--n-train", type=int, default=120)
    parser.add_argument("--coreset-ratio", type=float, default=0.02)
    args = parser.parse_args()
    train(model_name=args.model, tag=args.tag, image_size=args.image_size,
          n_train=args.n_train, coreset_ratio=args.coreset_ratio)


if __name__ == "__main__":
    main()
