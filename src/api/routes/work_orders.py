"""
work_orders.py — 工单查询与状态推进接口
========================================
补齐"维修执行"环节的最小闭环：
  AI 检测/预测自动建单 → 维修工接单(assign) → 维修中(start) → 完结(resolve，填写维修措施)
完结后供知识沉淀（memory_node / 人工审核归档到 knowledge_cases）。

状态机: pending → assigned → in_progress → resolved
（created 为 MES 同步态，本接口不直接产生；resolved 为终态）

接口:
  GET /api/v1/work-orders            工单列表（按状态/故障码过滤，维修工派单台）
  PUT /api/v1/work-orders/{id}       状态推进 {action: assign|start|resolve, resolution?}

权限: 查看需登录；assign/start 需维修工及以上；resolve 需维修工及以上（填写维修措施）。
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from src.api.routes.devices import _require_user
from src.api.schemas.response import APIResponse
from src.core.database import get_session_factory
from src.core.logging import get_logger
from src.data.models import WorkOrder
from src.security.rbac import UserRole

logger = get_logger(__name__)

router = APIRouter(prefix="/work-orders", tags=["工单"])

# 状态机：action → (允许的前置状态, 目标状态)
_TRANSITIONS = {
    "assign": ({"pending", "created"}, "assigned"),
    "start": ({"assigned"}, "in_progress"),
    "resolve": ({"assigned", "in_progress", "pending"}, "resolved"),
}

# 允许操作工单的最低角色：维修工
_MIN_WO_ROLE = UserRole.MAINTAINER


class WorkOrderActionRequest(BaseModel):
    action: str = Field(..., description="assign(接单) / start(开始维修) / resolve(完结)")
    resolution: str = Field(default="", max_length=2000, description="resolve 时必填：维修措施与结果")
    assignee: str = Field(default="", max_length=100, description="assign 时的维修工")


@router.get("")
async def list_work_orders(
    fastapi_req: Request,
    status: str = Query(default=""),
    fault_code: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """工单列表：维修工派单台/管理看板。按创建时间倒序。"""
    await _require_user(fastapi_req)
    factory = get_session_factory()
    async with factory() as db:
        q = select(WorkOrder)
        if status:
            q = q.where(WorkOrder.status == status)
        if fault_code:
            q = q.where(WorkOrder.fault_code == fault_code)
        rows = await db.execute(
            q.order_by(desc(WorkOrder.created_at)).offset((page - 1) * page_size).limit(page_size)
        )
        items = rows.scalars().all()
    data = [{
        "id": it.id, "mes_event_id": it.mes_event_id, "equipment_id": it.equipment_id,
        "fault_code": it.fault_code, "fault_desc": it.fault_desc,
        "priority": it.priority, "status": it.status, "assigned_to": it.assigned_to,
        "workshop_id": it.workshop_id, "diagnosis": it.diagnosis,
        "resolution": it.resolution, "created_at": str(it.created_at),
        "completed_at": str(it.completed_at) if it.completed_at else "",
    } for it in items]
    return APIResponse(data={"items": data, "page": page, "page_size": page_size}).model_dump()


@router.put("/{wo_id}")
async def transition_work_order(wo_id: int, req: WorkOrderActionRequest, fastapi_req: Request):
    """推进工单状态。resolve 必须填写维修措施（知识沉淀的输入）。"""
    user = await _require_user(fastapi_req, min_role=_MIN_WO_ROLE)
    if req.action not in _TRANSITIONS:
        raise HTTPException(422, f"action 必须是 {sorted(_TRANSITIONS)}")
    allowed_from, target = _TRANSITIONS[req.action]
    if req.action == "resolve" and not req.resolution.strip():
        raise HTTPException(422, "完结工单必须填写维修措施（resolution）")

    factory = get_session_factory()
    async with factory() as db:
        wo = (await db.execute(select(WorkOrder).where(WorkOrder.id == wo_id))).scalar_one_or_none()
        if wo is None:
            raise HTTPException(404, "工单不存在")
        if wo.status not in allowed_from:
            raise HTTPException(409, f"工单当前状态 {wo.status} 不允许 {req.action}（允许: {sorted(allowed_from)}）")

        wo.status = target
        if req.action == "assign":
            wo.assigned_to = req.assignee or user.user_id
        if req.action == "resolve":
            wo.resolution = req.resolution.strip()
            wo.completed_at = datetime.now().replace(microsecond=0)
        wo.updated_at = datetime.now().replace(microsecond=0)
        await db.commit()

    logger.info(f"工单状态推进: id={wo_id} {req.action} → {target} by {user.user_id}")
    return APIResponse(data={
        "id": wo_id, "status": target, "message": f"工单已{ {'assign': '接单', 'start': '开始维修', 'resolve': '完结'}[req.action] }",
    }).model_dump()
