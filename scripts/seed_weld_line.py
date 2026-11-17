"""seed_weld_line.py — 灌入锂电模组线焊接工位演示数据（巢湖 Line03）

场景: 新能源锂电模组线 OpMode7-EM040 激光焊接工位（TRUMPF 激光器 + Blackbird
振镜 + KUKA 焊接机器人 + 冷却水机），配合两条 AI 功能：

  PdM 侧 — 虚焊预警:
      4 台设备登记进 devices 台账（关键部件/循环折算/设备级阈值），
      遥测特征名与 TIA 程序 IF_HMI.Laser / IF_HMI.Galvo / IO_HMI 信号一一对应，
      真实产线由 OPC UA 网关按同名特征写入即可无缝替换本演示数据。

  视觉侧 — 焊缝检测:
      inspection_products 登记 2D 外观检测（patchcore）与 3D 形貌检测（efficientad，
      深度图渲染为灰度图送无监督异常检测），工位 OPMODE07-EM010。

遥测带渐进劣化曲线（振镜零漂、功率衰减、冷却劣化），供重训后的 RUL 模型演示
健康分衰减 → 自动预防性工单链路；当前 C-MAPSS 模型特征不兼容，仅用于链路联调。

用法（在 multi-agent-sys 项目根目录）:
    python scripts/seed_weld_line.py
    python scripts/seed_weld_line.py --cycles 300

幂等: 设备/换型映射已存在则跳过；遥测先清后写，重复执行结果一致。
"""
import argparse
import asyncio
import random

# ── 设备台账（对应 TIA 程序 OpMode7 焊接工位设备模块） ────────────

WELD_DEVICES = [
    {
        "device_id": "line03-weld-laser-01",
        "name": "激光焊接主机（TRUMPF + Blackbird）",
        "equipment_model": "TRUMPF TruDisk + Blackbird 振镜",
        "line": "巢湖Line03-模组线/OpMode7-EM040",
        "workshop_id": "workshop-a",
        "critical_component": "泵浦二极管/光学镜片",
        "cycles_per_day": 240.0,
        "rul_warn_cycles": 60,
    },
    {
        "device_id": "line03-weld-galvo-01",
        "name": "Blackbird 振镜焊接头",
        "equipment_model": "Blackbird Galvo Weld Head",
        "line": "巢湖Line03-模组线/OpMode7-EM040",
        "workshop_id": "workshop-a",
        "critical_component": "振镜电机/反射镜",
        "cycles_per_day": 240.0,
        "rul_warn_cycles": 80,
    },
    {
        "device_id": "line03-weld-cooling-01",
        "name": "激光器冷却水机",
        "equipment_model": "激光冷却水机（PHM_CoolingWaterMachine）",
        "line": "巢湖Line03-模组线/OpMode7-EM040",
        "workshop_id": "workshop-a",
        "critical_component": "水泵/压缩机",
        "cycles_per_day": 240.0,
        "rul_warn_cycles": 100,
    },
    {
        "device_id": "line03-weld-robot-01",
        "name": "KUKA 焊接机器人",
        "equipment_model": "KUKA（ROB_KUKA_CONTROL_PD_FB_Weld）",
        "line": "巢湖Line03-模组线/OpMode7-EM040",
        "workshop_id": "workshop-a",
        "critical_component": "减速机（J4/J6 轴）",
        "cycles_per_day": 240.0,
        "rul_warn_cycles": 120,
    },
]

# ── 质检换型映射（焊缝检测两个通道） ─────────────────────────────

WELD_INSPECTION_PRODUCTS = [
    {
        "product_code": "weld-seam-2d",
        "product_name": "焊缝外观2D检测（气孔/焊瘤/裂纹/飞溅/变色）",
        "model_name": "patchcore",
        "threshold": None,   # 用模型默认校准阈值；复盘后经 /inspections/stats 调整
        "station_id": "OPMODE07-EM010",
        "workshop_id": "workshop-a",
    },
    {
        "product_code": "weld-seam-3d",
        "product_name": "焊缝形貌3D检测（熔宽/余高/咬边，深度图渲染灰度图）",
        "model_name": "patchcore",
        "threshold": None,
        "station_id": "OPMODE07-EM010",
        "workshop_id": "workshop-a",
    },
]

# 每台设备的遥测生成配置: 特征名 -> (健康基线, 劣化幅度, 噪声σ, 保留小数位)
# 特征名对齐 TIA 程序信号（IF_HMI.Laser/Galvo、冷却水机、机器人轴数据）
_TELEMETRY_PROFILES = {
    "line03-weld-laser-01": {
        "laser_power_setpoint_w":   (4000.0,   0.0,    5.0,    0),
        "laser_power_actual_w":     (3980.0,  -95.0,   8.0,    0),
        "emission_delay_ms":        (0.2,      1.6,    0.05,   2),
        "laser_head_temp_c":        (32.0,     9.0,    0.4,    1),
        "coolant_temp_c":           (22.0,     4.5,    0.2,    1),
        "humidity_rh":              (45.0,     8.0,    0.6,    1),
        "monitoring_laser_alarms":  (0.0,      4.0,    0.05,   0),
        "monitoring_process_alarms": (0.0,     6.0,    0.07,   0),
    },
    "line03-weld-galvo-01": {
        "galvo_offset_x_um":        (2.0,     10.0,    0.15,   2),
        "galvo_offset_y_um":        (1.5,      8.5,    0.15,   2),
        "galvo_offset_angle_urad":  (5.0,     55.0,    1.2,    1),
        "galvo_temp_c":             (28.0,     8.0,    0.3,    1),
        "weld_count":               (0.0,      0.0,    0.0,    0),   # 累计值, 单独处理
    },
    "line03-weld-cooling-01": {
        "water_inlet_temp_c":       (20.0,     4.0,    0.15,   1),
        "water_outlet_temp_c":      (23.0,     5.0,    0.2,    1),
        "water_flow_l_min":         (25.0,    -4.0,    0.25,   1),
        "pump_pressure_bar":        (4.0,     -0.7,    0.03,   2),
        "compressor_current_a":     (8.0,      1.5,    0.08,   2),
    },
    "line03-weld-robot-01": {
        "axis4_current_a":          (12.0,     4.0,    0.15,   2),
        "axis6_current_a":          (10.0,     3.5,    0.12,   2),
        "path_deviation_mm":        (0.05,     0.17,   0.01,   3),
        "servo_temp_c":             (45.0,     9.0,    0.35,   1),
    },
}

_KNEE_RATIO = 0.6   # 劣化拐点：前 60% 循环健康，之后渐进劣化


def generate_weld_telemetry(device_id: str, cycles: int = 300, seed: int = 42) -> list[dict]:
    """生成单台焊接设备的多传感器遥测（纯函数，确定性输出，便于测试）。

    每条含 cycle(int) + 与 TIA 信号同名的特征列；劣化从 60% 处开始渐进，
    各特征按配置方向漂移（功率/流量下降，温度/偏移/延迟上升）。
    """
    profile = _TELEMETRY_PROFILES.get(device_id)
    if profile is None:
        raise ValueError(f"未登记的焊接工位设备: {device_id}")

    rng = random.Random(f"{seed}:{device_id}")
    knee = int(cycles * _KNEE_RATIO)
    readings = []
    for cycle in range(1, cycles + 1):
        progress = max(0.0, (cycle - knee) / max(1, cycles - knee))
        drift = progress ** 1.5
        row = {"cycle": cycle}
        for feat, (base, amp, sigma, ndigits) in profile.items():
            if feat == "weld_count":
                row[feat] = cycle          # 累计焊接次数 = 循环序号
                continue
            val = base + amp * drift + rng.gauss(0.0, sigma)
            if ndigits == 0:
                val = round(val)
            else:
                val = round(val, ndigits)
            row[feat] = val
        readings.append(row)
    return readings


async def seed(cycles: int = 300) -> None:
    from sqlalchemy import delete, select

    from src.core.database import get_session_factory, init_db
    from src.core.logging import get_logger
    from src.data.models import Device, DeviceTelemetry, InspectionProduct

    logger = get_logger(__name__)
    await init_db()
    factory = get_session_factory()

    # 1. 设备台账（虚焊预警的业务翻译依据）
    async with factory() as db:
        for dev in WELD_DEVICES:
            exists = (await db.execute(
                select(Device).where(Device.device_id == dev["device_id"])
            )).scalar_one_or_none()
            if not exists:
                db.add(Device(**dev))
                logger.info(f"设备台账已登记: {dev['device_id']}（{dev['critical_component']}，"
                            f"{dev['cycles_per_day']:g} 循环/天，预警阈值 {dev['rul_warn_cycles']} 循环）")
        await db.commit()

    # 2. 遥测（先清后写；特征名与 TIA IF_HMI 信号对应，OPC UA 接入时同名替换）
    for dev in WELD_DEVICES:
        rows = generate_weld_telemetry(dev["device_id"], cycles=cycles)
        async with factory() as db:
            await db.execute(delete(DeviceTelemetry).where(
                DeviceTelemetry.device_id == dev["device_id"]))
            db.add_all([
                DeviceTelemetry(device_id=dev["device_id"], cycle=r.pop("cycle"), readings=r)
                for r in rows
            ])
            await db.commit()
        logger.info(f"设备 {dev['device_id']} 遥测已灌入 {len(rows)} 条（含渐进劣化曲线）")

    # 3. 质检换型映射（焊缝检测 2D/3D 双通道）
    async with factory() as db:
        for prod in WELD_INSPECTION_PRODUCTS:
            exists = (await db.execute(
                select(InspectionProduct).where(
                    InspectionProduct.product_code == prod["product_code"])
            )).scalar_one_or_none()
            if not exists:
                db.add(InspectionProduct(**prod))
                logger.info(f"质检换型映射已登记: {prod['product_code']} → "
                            f"{prod['model_name']} @ {prod['station_id']}")
        await db.commit()


if __name__ == "__main__":
    from src.core.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(description="灌入锂电模组线焊接工位演示数据（台账+遥测+换型映射）")
    parser.add_argument("--cycles", type=int, default=300, help="每台设备遥测循环数（默认 300）")
    args = parser.parse_args()
    asyncio.run(seed(cycles=args.cycles))
