"""sync_ml_models.py — 把两个训练项目的模型产物同步到本仓库 models/ 目录。

docker-compose 的 ml-vision / ml-rul 服务把 models/vision、models/rul 挂载为容器内
/app/results。训练项目更新检查点后重跑本脚本即可（幂等，增量复制）。

用法（multi-agent-sys 项目根目录）:
    python scripts/sync_ml_models.py
    python scripts/sync_ml_models.py --vision-src E:\\path\\to\\industrial-anomaly-detection \\
        --rul-src D:\\path\\to\\predictive-maintenance-rul
"""
import argparse
import shutil
from pathlib import Path

DEFAULT_VISION_SRC = Path(r"E:\PycharmProjects\industrial-anomaly-detection")
DEFAULT_RUL_SRC = Path(r"D:\KimiData\kimi\tasks\2026-11-07\18-25-44-6d03cff0\predictive-maintenance-rul")
MODELS_DIR = Path(__file__).parent.parent / "models"


def sync_dir(src: Path, dst: Path, patterns: list[str], label: str) -> int:
    """按 glob 模式把 src 下匹配文件复制到 dst（保持子目录结构）。"""
    n = 0
    for pat in patterns:
        for f in src.glob(pat):
            if f.is_dir():
                continue
            rel = f.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.stat().st_mtime >= f.stat().st_mtime:
                continue  # 目标更新或相同，跳过
            shutil.copy2(f, target)
            n += 1
            print(f"  [{label}] {rel} -> {target}")
    return n


def main():
    parser = argparse.ArgumentParser(description="同步 ML 模型产物到 models/")
    parser.add_argument("--vision-src", default=str(DEFAULT_VISION_SRC))
    parser.add_argument("--rul-src", default=str(DEFAULT_RUL_SRC))
    args = parser.parse_args()

    vision_src = Path(args.vision_src)
    rul_src = Path(args.rul_src)

    total = 0
    if vision_src.exists():
        # 检查点 + 热力图输出目录结构（agent_inspections 不同步，仅权重与指标）
        total += sync_dir(vision_src / "results", MODELS_DIR / "vision",
                          ["*/weights/**/*.ckpt", "*/test_metrics.json"], "vision")
    else:
        print(f"  [vision] 源目录不存在，跳过: {vision_src}")

    if rul_src.exists():
        total += sync_dir(rul_src / "results" / "checkpoints", MODELS_DIR / "rul",
                          ["*.pt", "*.json"], "rul")
    else:
        print(f"  [rul] 源目录不存在，跳过: {rul_src}")

    print(f"同步完成：{total} 个文件 -> {MODELS_DIR}")


if __name__ == "__main__":
    main()
