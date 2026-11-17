"""synth_weld.py — 焊缝 2D/3D 合成图像生成器（含缺陷注入）
======================================================
真实焊缝图片需要产线采集与标注；为使视觉链路端到端可验证，用程序合成：
  - 2D 灰度外观图: 拉丝铝基材 + 高亮焊道（高斯截面亮带 + 波纹 + 走向摆动）
  - 3D 深度图:     焊道高度场（高斯脊）渲染为 16bit→8bit 灰度
缺陷类型（与 SOP-LW-07 外观判定对应）:
  pore 气孔 / crack 裂纹 / spatter 飞溅 / undercut 咬边 /
  lack_of_fusion 未熔合(焊道细窄) / oxidation 氧化变色 / excess_reinforcement 余高过高

生成器确定性（seed 固定可复现）；train 只出正常品，test 出正常+缺陷并带标签，
供 anomalib 无监督训练与阈值/指标评估。
"""
from __future__ import annotations

import math
import random

import numpy as np
from PIL import Image

IMG_SIZE = 256
DEFECT_TYPES = ["pore", "crack", "spatter", "undercut", "lack_of_fusion", "oxidation", "excess_reinforcement"]

# 像素→毫米标定（3D 测量用）
MM_PER_PX = 0.1
# 深度场标定：深度单位 → 毫米（正常余高 ~0.42-0.60mm）
MM_PER_UNIT = 0.01
DEPTH_FLOOR, DEPTH_CEIL = -20.0, 80.0     # 固定映射区间，保证 PNG 可逆


def depth_to_gray(depth: np.ndarray) -> np.ndarray:
    """深度数组 → 8bit 灰度（固定线性映射，可逆）。"""
    g = (depth - DEPTH_FLOOR) / (DEPTH_CEIL - DEPTH_FLOOR) * 255.0
    return np.clip(g, 0, 255).astype(np.uint8)


def gray_to_depth(gray: np.ndarray) -> np.ndarray:
    """8bit 灰度 → 深度数组（还原毫米前的单位值）。"""
    return gray.astype(np.float32) / 255.0 * (DEPTH_CEIL - DEPTH_FLOOR) + DEPTH_FLOOR


def _aluminum_background(rng: random.Random) -> np.ndarray:
    """拉丝铝基材：水平纹理噪声（正常类紧致度决定异常分离度，方差要收）。"""
    np_rng = np.random.default_rng(rng.randrange(2 ** 32))
    base = rng.uniform(96, 106)
    img = np.zeros((IMG_SIZE, IMG_SIZE), dtype=np.float32)
    row_noise = np_rng.normal(0, 2.0, IMG_SIZE).astype(np.float32)
    img += base + row_noise[:, None]
    img += np_rng.normal(0, 1.2, (IMG_SIZE, IMG_SIZE)).astype(np.float32)
    # 水平拉丝纹
    for _ in range(40):
        y = rng.randint(0, IMG_SIZE - 1)
        img[y, :] += rng.uniform(-4, 4)
    return img


def _bead_params(rng: random.Random, defective: bool, defect: str | None) -> dict:
    width = rng.uniform(36, 44)                      # 正常熔宽 px
    if defective and defect == "lack_of_fusion":
        width *= rng.uniform(0.45, 0.62)             # 未熔合: 熔宽明显变窄
    if defective and defect == "excess_reinforcement":
        width *= rng.uniform(1.45, 1.7)              # 余高过高: 焊道过宽
    return {
        "width": width,
        "center": rng.uniform(0.48, 0.52) * IMG_SIZE,
        "amp": rng.uniform(30, 38) * (1.5 if defective and defect == "excess_reinforcement" else 1.0),
        "ripple": rng.uniform(5.0, 7.0),
        "wander": rng.uniform(1.5, 3.0),
        "phase": rng.uniform(0, 2 * math.pi),
    }


def _render_bead(img: np.ndarray, depth: np.ndarray, p: dict) -> None:
    ys = np.arange(IMG_SIZE, dtype=np.float32)
    center_line = p["center"] + p["wander"] * np.sin(2 * math.pi * ys / IMG_SIZE * 2 + p["phase"])
    half = p["width"] / 2
    ripple = 1 + 0.08 * np.sin(2 * math.pi * ys / p["ripple"] + p["phase"])
    for y in range(IMG_SIZE):
        c = center_line[y]
        xs = np.arange(IMG_SIZE, dtype=np.float32)
        prof = np.exp(-0.5 * ((xs - c) / (half * ripple[y])) ** 2)
        img[y] += p["amp"] * prof
        depth[y] += (p["amp"] / 40.0) * 60.0 * prof   # 高度场（相对 0.6mm 余高）


def _inject_defect(img: np.ndarray, depth: np.ndarray, rng: random.Random, defect: str) -> None:
    if defect == "pore":
        for _ in range(rng.randint(1, 3)):
            cx, cy = rng.randint(90, 166), rng.randint(90, 166)
            r = rng.uniform(4, 10)
            yy, xx = np.ogrid[:IMG_SIZE, :IMG_SIZE]
            m = ((xx - cx) ** 2 + (yy - cy) ** 2) < r * r
            img[m] -= rng.uniform(70, 110)            # 气孔: 深色斑点
            depth[m] -= rng.uniform(15, 30)
    elif defect == "crack":
        x, y = rng.randint(110, 146), rng.randint(30, 60)
        for _ in range(rng.randint(50, 90)):
            img[y, x] -= rng.uniform(70, 100)        # 裂纹: 暗色细线随机游走
            depth[y, x] -= rng.uniform(20, 35)
            x += rng.choice([-1, 0, 1])
            y = min(IMG_SIZE - 1, y + 1)
    elif defect == "spatter":
        for _ in range(rng.randint(6, 14)):
            cx, cy = rng.randint(10, 246), rng.choice([rng.randint(10, 70), rng.randint(186, 246)])
            r = rng.uniform(1.5, 4)
            yy, xx = np.ogrid[:IMG_SIZE, :IMG_SIZE]
            m = ((xx - cx) ** 2 + (yy - cy) ** 2) < r * r
            img[m] += rng.uniform(40, 70)              # 飞溅: 焊道外亮点
    elif defect == "undercut":
        side = rng.choice([-1, 1])
        for y in range(IMG_SIZE):
            c = 128 + side * rng.uniform(18, 24)
            for x in range(int(c) - 4, int(c) + 4):
                if 0 <= x < IMG_SIZE:
                    img[y, x] -= rng.uniform(45, 65)   # 咬边: 焊趾沟槽
                    depth[y, x] -= rng.uniform(30, 45)
    elif defect == "oxidation":
        yy, xx = np.ogrid[:IMG_SIZE, :IMG_SIZE]
        m = ((xx - 128) ** 2 + (yy - 128) ** 2) < rng.uniform(60, 90) ** 2
        img[m] -= rng.uniform(30, 50)                  # 氧化变色: 区域性变暗发蓝
    # lack_of_fusion / excess_reinforcement 已在焊道参数中体现


def render_weld_image(rng: random.Random, defective: bool = False,
                      defect: str | None = None) -> tuple[Image.Image, np.ndarray, str]:
    """返回 (2D 外观图, 3D 深度数组 float32, 标签)。"""
    if defective and defect not in DEFECT_TYPES:
        raise ValueError(f"未知缺陷类型: {defect}")
    img = _aluminum_background(rng)
    depth = np.zeros((IMG_SIZE, IMG_SIZE), dtype=np.float32)
    p = _bead_params(rng, defective, defect)
    _render_bead(img, depth, p)
    label = "good"
    if defective and defect:
        _inject_defect(img, depth, rng, defect)
        label = defect
    img = np.clip(img, 0, 255).astype(np.uint8)
    return Image.fromarray(img, mode="L").convert("RGB"), depth, label


def render_depth_image(depth: np.ndarray) -> Image.Image:
    """深度数组 → 8bit 灰度图（3D 检测输入；固定线性映射可逆，灰度=高度）。"""
    return Image.fromarray(depth_to_gray(depth), mode="L").convert("RGB")


def measure_bead(depth: np.ndarray) -> dict:
    """从深度图测量焊缝几何量（3D 通道的规则判定，毫米单位）。

    取图像中部的水平剖面（焊道走向沿 y 轴，横截面在 x 方向）：
    半高宽=熔宽，峰顶-基材=余高，焊趾区最低点相对基材=咬边深度。
    """
    row = depth[depth.shape[0] // 2, :].astype(np.float32)
    base = float(np.median(np.concatenate([row[:40], row[-40:]])))   # 基材高度
    peak = float(row.max())
    half = base + (peak - base) / 2
    above = np.where(row >= half)[0]
    width_px = int(above[-1] - above[0]) if len(above) >= 2 else 0
    reinforcement_mm = (peak - base) * MM_PER_UNIT
    if len(above) >= 2:
        lo, hi = max(0, above[0] - 6), min(len(row), above[-1] + 6)
        toe_min = float(row[lo:hi].min())
    else:
        toe_min = base
    undercut_mm = max(0.0, (base - toe_min) * MM_PER_UNIT)
    return {
        "bead_width_mm": round(width_px * MM_PER_PX, 2),
        "reinforcement_mm": round(reinforcement_mm, 2),
        "undercut_mm": round(undercut_mm, 2),
    }


# 几何判定容差（SOP 工艺窗口）
GEO_LIMITS = {"bead_width_mm": (3.0, 5.5), "reinforcement_mm": (0.15, 0.7), "undercut_mm": (0.0, 0.15)}


def geometry_verdict(measures: dict) -> tuple[bool, list[str]]:
    """几何量超窗 → (False, 超窗项列表)；窗内 → (True, [])。"""
    violations = [
        f"{k}={v}mm 超窗 {GEO_LIMITS[k]}"
        for k, v in measures.items()
        if not (GEO_LIMITS[k][0] <= v <= GEO_LIMITS[k][1])
    ]
    return (not violations), violations


def generate_dataset(root: str, n_train: int = 240, n_test_good: int = 40,
                     n_test_per_defect: int = 12, seed: int = 42,
                     out_size: int = 128) -> dict:
    """生成 anomalib Folder 布局数据集，返回统计信息。

    out_size: 输出图片边长（训练速度关键；PatchCore coreset 复杂度随像素数平方增长，
    CPU 训练用 128 足够——缺陷尺度相对焊道宽度占比大，且不用于测量标定）。
    """
    from pathlib import Path

    root = Path(root)
    rng = random.Random(seed)
    stats = {"train_good": 0, "test_good": 0, "test_defective": 0}
    for split, n, defective in [("train/good", n_train, False), ("test/good", n_test_good, False)]:
        out = root / split
        out.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            img, depth, _ = render_weld_image(rng, defective=False)
            img = img.resize((out_size, out_size))
            img.save(out / f"{split.replace('/', '_')}_{i:04d}.png")
            stats["train_good" if "train" in split else "test_good"] += 1
    out = root / "test" / "defective"
    out.mkdir(parents=True, exist_ok=True)
    for defect in DEFECT_TYPES:
        for i in range(n_test_per_defect):
            img, depth, _ = render_weld_image(rng, defective=True, defect=defect)
            img = img.resize((out_size, out_size))
            img.save(out / f"{defect}_{i:03d}.png")
            stats["test_defective"] += 1
    # 3D 形貌通道：同分布另出一份深度图数据集（灰度=高度）
    droot = Path(str(root) + "_depth")
    rng3 = random.Random(seed + 1)
    for split, n, defective in [("train/good", n_train, False), ("test/good", n_test_good, False)]:
        out = droot / split
        out.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            _, depth, _ = render_weld_image(rng3, defective=False)
            render_depth_image(depth).resize((out_size, out_size)).save(
                out / f"d_{split.replace('/', '_')}_{i:04d}.png")
    out = droot / "test" / "defective"
    out.mkdir(parents=True, exist_ok=True)
    for defect in DEFECT_TYPES:
        for i in range(n_test_per_defect):
            _, depth, _ = render_weld_image(rng3, defective=True, defect=defect)
            render_depth_image(depth).resize((out_size, out_size)).save(out / f"d_{defect}_{i:03d}.png")
    return stats
