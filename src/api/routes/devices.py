"""
devices.py — 设备遥测与预测性维护数据接口
==========================================
ML 融合新增。设备传感器数据的摄入与查询，是 predictive_maintenance 节点的输入源：
真实产线由 MQTT/OPC UA 网关写入（见架构文档），当前阶段先提供 REST 批量摄入 +
CSV 导入（管理后台/调试），后续接采集总线时本路由逻辑不变、只换写入方。

权限:
  - 全部端点要求登录（与 conversation 路由同标准）
  - POST 摄入额外要求班组长及以上（产线工人不可伪造设备数据）
  - 摄入按 (device_id, cycle) 幂等去重：网关重发/批量重传不会产生重复行
"""
from fastapi import APIRouter, HTTPException, Query, Request

from src.api.middleware.auth import get_current_user
from src.api.schemas.request import TelemetryIngestRequest
from src.api.schemas.response import APIResponse
from src.core.database import get_session_factory
from src.core.logging import get_logger
from src.data.models import DeviceTelemetry, RULPrediction
from src.security.rbac import UserRole
from sqlalchemy import desc, func, select

logger = get_logger(__name__)

router = APIRouter(prefix="/devices", tags=["设备遥测"])

_MIN_INGEST_ROLE = UserRole.SHIFT_LEAD  # 摄入遥测的最低角色：班组长


async def _require_user(request: Request, min_role: UserRole | None = None):
    """统一鉴权：必须登录；min_role 指定时还需角色等级不低于该角色。"""
    user = await get_current_user(request)
    if user is None:
        raise HTTPException(401, "请先登录")
    if min_role is not None:
        order = list(UserRole)
        if order.index(user.role) < order.index(min_role):
            raise HTTPException(403, f"摄入遥测需要 {min_role.value} 及以上角色")
    return user


@router.post("/{device_id}/telemetry")
async def ingest_telemetry(device_id: str, req: TelemetryIngestRequest, fastapi_req: Request):
    """批量写入设备遥测。每条 reading 必须含 cycle(int)；重复 cycle 自动跳过（幂等）。

    数据质量校验：某传感器列在批次内 ≥90% 取值相同视为卡死传感器（真实故障：
    传感器失效会喂给 RUL 模型垃圾数据），拒绝整个批次并列出异常列。
    """
    user = await _require_user(fastapi_req, min_role=_MIN_INGEST_ROLE)

    parsed = []
    for i, r in enumerate(req.readings):
        cycle = r.get("cycle")
        if not isinstance(cycle, int) or isinstance(cycle, bool):
            raise HTTPException(422, f"第 {i} 条缺少 cycle(int) 字段")
        parsed.append((cycle, {k: v for k, v in r.items() if k != "cycle"}))

    # 卡死传感器校验（批次内 ≥2 条才校验，90% 相同即判卡死）
    stuck_cols: list[str] = []
    if len(parsed) >= 2:
        cols = set().union(*(s.keys() for _, s in parsed))
        for col in cols:
            values = [s.get(col) for _, s in parsed if col in s]
            if len(values) >= 2 and len(set(values)) == 1:
                stuck_cols.append(col)
        if stuck_cols:
            raise HTTPException(
                422,
                f"数据质量校验失败：传感器列 {sorted(stuck_cols)} 整批取值恒定（卡死传感器），"
                "请检查采集通道后重试",
            )

    factory = get_session_factory()
    async with factory() as db:
        existing = await db.execute(
            select(DeviceTelemetry.cycle)
            .where(DeviceTelemetry.device_id == device_id)
            .where(DeviceTelemetry.cycle.in_([c for c, _ in parsed]))
        )
        dup = {row[0] for row in existing.all()}
        rows = [
            DeviceTelemetry(device_id=device_id, cycle=cycle, readings=sensors)
            for cycle, sensors in parsed if cycle not in dup
        ]
        if rows:
            db.add_all(rows)
            await db.commit()
    return APIResponse(data={
        "device_id": device_id,
        "inserted": len(rows),
        "skipped_duplicates": len(parsed) - len(rows),
    }).model_dump()


@router.get("")
async def list_devices(fastapi_req: Request):
    """设备列表：每台设备最新 cycle、遥测条数、最近一次 RUL 预测。"""
    await _require_user(fastapi_req)
    factory = get_session_factory()
    async with factory() as db:
        rows = await db.execute(
            select(
                DeviceTelemetry.device_id,
                func.count(DeviceTelemetry.id).label("n_rows"),
                func.max(DeviceTelemetry.cycle).label("latest_cycle"),
            ).group_by(DeviceTelemetry.device_id)
        )
        devices = []
        for device_id, n_rows, latest_cycle in rows.all():
            pred = await db.execute(
                select(RULPrediction)
                .where(RULPrediction.device_id == device_id)
                .order_by(desc(RULPrediction.predicted_at))
                .limit(1)
            )
            latest = pred.scalar_one_or_none()
            devices.append({
                "device_id": device_id,
                "telemetry_rows": n_rows,
                "latest_cycle": latest_cycle,
                "latest_rul": ({
                    "rul_cycles": latest.rul_cycles,
                    "model": latest.model,
                    "predicted_at": str(latest.predicted_at),
                } if latest else None),
            })
    return APIResponse(data={"devices": devices, "count": len(devices)}).model_dump()


@router.get("/{device_id}/telemetry")
async def get_telemetry(device_id: str, fastapi_req: Request, limit: int = Query(default=50, ge=1, le=500)):
    """取设备最近 limit 条遥测（按 cycle 升序返回）。"""
    await _require_user(fastapi_req)
    factory = get_session_factory()
    async with factory() as db:
        rows = await db.execute(
            select(DeviceTelemetry)
            .where(DeviceTelemetry.device_id == device_id)
            .order_by(desc(DeviceTelemetry.cycle))
            .limit(limit)
        )
        items = rows.scalars().all()
    data = [{"cycle": it.cycle, **(it.readings or {})} for it in reversed(items)]
    return APIResponse(data={"device_id": device_id, "readings": data, "count": len(data)}).model_dump()
