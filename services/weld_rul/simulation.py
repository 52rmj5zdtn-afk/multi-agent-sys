"""simulation.py — 焊接工位 run-to-failure 仿真器
==============================================

真实产线没有"跑到坏"的全寿命数据，训练 RUL 模型需要仿真补齐：
每台设备按 seed_weld_line 的信号定义（与 TIA 程序 IF_HMI 同名）生成
从健康到失效的完整循环序列，失效点 = 劣化进度 100% 的循环。

仿真假设（与 SOP-LW-07 对齐）:
  - 设备基线有 ±3% 个体差异，劣化速率 0.8-1.2 倍随机（不同装机/工况）
  - 拐点位于全寿命的 25%-55%，之后 progress^1.5 渐进劣化
  - 特征到达 base+amp 即失效（如 galvo_offset_x 到达 12µm，对应 SOP 停机校准线）

输出给 dataset.py 做滑窗；RUL 标签 = 失效循环 - 当前循环。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

# scripts/ 不是包：按路径加入后从 seed_weld_line 导入信号定义（单一来源）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from seed_weld_line import _TELEMETRY_PROFILES  # noqa: E402

DEVICE_IDS: list[str] = list(_TELEMETRY_PROFILES)

# 失效判据的业务解释（元信息，训练标签仍是 RUL 回归）
FAILURE_NOTES = {
    "line03-weld-laser-01": "实际功率衰减≥2.5% 或激光头温度≥41℃（泵浦/光学件失效）",
    "line03-weld-galvo-01": "振镜偏移≥12µm 或偏角≥60µrad（光路偏移超差）",
    "line03-weld-cooling-01": "水流量≤21L/min 或泵压≤3.3bar（冷却失效）",
    "line03-weld-robot-01": "轨迹偏差≥0.22mm（减速机间隙超限）",
}


def simulate_unit(device_id: str, rng: random.Random) -> list[dict]:
    """仿真单台设备从投运到失效的全寿命遥测。"""
    profile = _TELEMETRY_PROFILES[device_id]
    life = rng.randint(180, 560)
    knee = int(life * rng.uniform(0.25, 0.55))
    base_jitter = {f: rng.uniform(0.97, 1.03) for f in profile}
    amp_jitter = rng.uniform(0.8, 1.2)
    rows = []
    for cycle in range(1, life + 1):
        progress = max(0.0, (cycle - knee) / max(1, life - knee))
        drift = progress ** 1.5
        row = {"cycle": cycle}
        for feat, (base, amp, sigma, ndigits) in profile.items():
            if feat == "weld_count":
                row[feat] = cycle
                continue
            val = base * base_jitter[feat] + amp * amp_jitter * drift + rng.gauss(0.0, sigma)
            row[feat] = round(val) if ndigits == 0 else round(val, ndigits)
        rows.append(row)
    return rows


def simulate_fleet(device_id: str, n_units: int, seed: int = 42) -> list[list[dict]]:
    """仿真一机群（n_units 台同型设备），返回每台的全寿命序列。"""
    rng = random.Random(f"{seed}:{device_id}")
    return [simulate_unit(device_id, rng) for _ in range(n_units)]
