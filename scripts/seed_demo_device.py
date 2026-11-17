"""seed_demo_device.py — 灌入演示数据：设备台账 + 遥测 + 质检换型映射。

工业场景化演示所需的三个基础数据集：
  - devices:               demo-compressor-001（1号激光器/泵浦模块，C-MAPSS 退化数据类比演示，每天 24 循环）
  - device_telemetry:      C-MAPSS FD001 测试集 18 号发动机最后 80 个循环（低 RUL，会触发预警）
  - inspection_products:   bottle → patchcore（MVTec 瓶体外观质检工位）

真实产线由 MQTT/OPC UA 网关写入遥测，本脚本仅用于本地演示/联调。

用法（在 multi-agent-sys 项目根目录）:
    python scripts/seed_demo_device.py
    python scripts/seed_demo_device.py --device xxx --csv path/to.csv

幂等：重复执行先清旧数据再写入。
"""
import argparse
import asyncio
import csv
from pathlib import Path

from sqlalchemy import delete, select

from src.core.database import get_session_factory
from src.core.logging import setup_logging, get_logger
from src.data.models import Device, DeviceTelemetry, InspectionProduct

logger = get_logger(__name__)

DEFAULT_CSV = Path(__file__).parent.parent / "data" / "seeds" / "telemetry_demo.csv"
DEFAULT_DEVICE = "demo-compressor-001"


async def seed(device_id: str, csv_path: Path):
    factory = get_session_factory()

    # 1. 设备台账（演示机台：真实 PdM 的业务翻译依据）
    async with factory() as db:
        exists = (await db.execute(select(Device).where(Device.device_id == device_id))).scalar_one_or_none()
        if not exists:
            db.add(Device(
                device_id=device_id,
                name="1号激光器",
                equipment_model="C-MAPSS-FD001-DEMO",
                line="动力线-1",
                workshop_id="workshop-a",
                critical_component="泵浦二极管",
                cycles_per_day=24.0,
                rul_warn_cycles=50,
            ))
            await db.commit()
            logger.info(f"设备台账已登记: {device_id}（1号激光器 / 泵浦模块 / C-MAPSS 类比演示 / 每天24循环）")

    # 2. 遥测数据
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    readings = []
    for r in rows:
        cycle = int(r.pop("cycle"))
        readings.append(DeviceTelemetry(
            device_id=device_id,
            cycle=cycle,
            readings={k: float(v) for k, v in r.items() if v not in (None, "")},
        ))
    async with factory() as db:
        await db.execute(delete(DeviceTelemetry).where(DeviceTelemetry.device_id == device_id))
        db.add_all(readings)
        await db.commit()
    logger.info(f"设备 {device_id} 遥测已灌入 {len(readings)} 条（cycle {readings[0].cycle}..{readings[-1].cycle}）")

    # 3. 质检换型映射（bottle → patchcore；多品类时增行即可）
    async with factory() as db:
        prod = (await db.execute(
            select(InspectionProduct).where(InspectionProduct.product_code == "bottle")
        )).scalar_one_or_none()
        if not prod:
            db.add(InspectionProduct(
                product_code="bottle",
                product_name="瓶体（演示品类，MVTec AD）",
                model_name="patchcore",
                threshold=None,          # 用模型默认校准阈值；产品阈值在 inspections/stats 复盘后调整
                station_id="vision-station-1",
                workshop_id="workshop-a",
            ))
            await db.commit()
            logger.info("质检换型映射已登记: bottle → patchcore @ vision-station-1")


if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser(description="灌入演示设备数据（台账+遥测+换型映射）")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="设备 ID")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="遥测 CSV 路径（列: cycle + 特征名）")
    args = parser.parse_args()
    asyncio.run(seed(args.device, Path(args.csv)))
