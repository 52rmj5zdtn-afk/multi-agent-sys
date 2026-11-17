"""
inspections.py — 视觉质检记录与人工复核接口
============================================
工业 AI 上线必备的人在回路（human-in-the-loop）：
  AI 判定 NG 不直接等于报废/停机——质检员复核（确认缺陷 / 误报），
  误报率统计反哺阈值校准（漏检成本高的产品调保守）。

接口:
  GET  /api/v1/inspections                 质检台列表（分页 + 复核状态过滤）
  POST /api/v1/inspections/{id}/review     复核：confirmed_defect / false_positive
  GET  /api/v1/inspections/stats           误报率统计（按产品）

权限: 复核需班组长及以上（质检判定权）；列表查看需登录。
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, func, select

from src.api.routes.devices import _require_user
from src.api.schemas.response import APIResponse
from src.core.database import get_session_factory
from src.core.logging import get_logger
from src.data.models import InspectionRecord
from src.observability.metrics import record_inspection_review
from src.security.rbac import UserRole

logger = get_logger(__name__)

router = APIRouter(prefix="/inspections", tags=["视觉质检"])

_REVIEW_RESULTS = {"confirmed_defect", "false_positive"}


class ReviewRequest(BaseModel):
    result: str = Field(..., description="复核结论: confirmed_defect / false_positive")
    note: str = Field(default="", max_length=500)


def _bool_count(column_condition) -> case:
    """可移植的布尔计数：SQLite / PostgreSQL 通用（避免方言特化的 cast 写法）。"""
    return case((column_condition, 1), else_=0)


@router.get("")
async def list_inspections(
    fastapi_req: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    review_status: str = Query(default="", description="过滤: pending / confirmed_defect / false_positive"),
    product_code: str = Query(default=""),
):
    """质检台列表：可按复核状态过滤（默认全部，待复核优先人工处理）。"""
    await _require_user(fastapi_req)
    factory = get_session_factory()
    async with factory() as db:
        q = select(InspectionRecord)
        if review_status == "pending":
            q = q.where(InspectionRecord.review_result == "")
        elif review_status in _REVIEW_RESULTS:
            q = q.where(InspectionRecord.review_result == review_status)
        if product_code:
            q = q.where(InspectionRecord.product_code == product_code)
        total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar()
        rows = await db.execute(
            q.order_by(desc(InspectionRecord.created_at))
            .offset((page - 1) * page_size).limit(page_size)
        )
        items = rows.scalars().all()
    data = [{
        "id": it.id, "image_url": it.image_url, "model": it.model,
        "product_code": it.product_code, "station_id": it.station_id,
        "defect_detected": it.defect_detected, "anomaly_score": it.anomaly_score,
        "threshold": it.threshold, "heatmap_path": it.heatmap_path,
        "work_order_id": it.work_order_id,
        "review_result": it.review_result or "pending",
        "reviewed_by": it.reviewed_by, "reviewed_at": str(it.reviewed_at) if it.reviewed_at else "",
        "created_at": str(it.created_at),
    } for it in items]
    return APIResponse(data={"items": data, "total": total, "page": page, "page_size": page_size}).model_dump()


@router.post("/{record_id}/review")
async def review_inspection(record_id: int, req: ReviewRequest, fastapi_req: Request):
    """人工复核 AI 判定。误报率统计反哺阈值校准。"""
    user = await _require_user(fastapi_req, min_role=UserRole.SHIFT_LEAD)
    if req.result not in _REVIEW_RESULTS:
        raise HTTPException(422, f"result 必须是 {sorted(_REVIEW_RESULTS)}")

    factory = get_session_factory()
    async with factory() as db:
        rec = (await db.execute(
            select(InspectionRecord).where(InspectionRecord.id == record_id)
        )).scalar_one_or_none()
        if rec is None:
            raise HTTPException(404, "质检记录不存在")
        if rec.review_result:
            raise HTTPException(409, f"该记录已复核为 {rec.review_result}，不可重复复核")
        rec.review_result = req.result
        rec.reviewed_by = user.user_id
        rec.reviewed_at = datetime.now().replace(microsecond=0)
        await db.commit()

    record_inspection_review(req.result, rec.product_code or "unknown")
    logger.info(f"质检复核: record={record_id} → {req.result} by {user.user_id} note={req.note[:50]}")
    return APIResponse(data={
        "id": record_id, "review_result": req.result,
        "message": "已确认为真实缺陷" if req.result == "confirmed_defect" else "已登记为误报（将计入误报率统计）",
    }).model_dump()


@router.get("/stats")
async def inspection_stats(fastapi_req: Request):
    """误报率统计：按产品分组的总检测数 / NG数 / 复核数 / 误报数 / 误报率。

    运营用法：某产品误报率持续 > 20% → 调高其 inspection_products.threshold；
    确认缺陷多而 AI 判不出（confirmed_defect 但 defect_detected=false 的漏检）→ 调低阈值或重训模型。
    """
    await _require_user(fastapi_req)
    factory = get_session_factory()
    async with factory() as db:
        rows = await db.execute(
            select(
                InspectionRecord.product_code,
                func.count(InspectionRecord.id).label("total"),
                func.sum(_bool_count(InspectionRecord.defect_detected.is_(True))).label("ng"),
                func.sum(_bool_count(InspectionRecord.review_result != "")).label("reviewed"),
                func.sum(_bool_count(InspectionRecord.review_result == "false_positive")).label("false_positive"),
            ).group_by(InspectionRecord.product_code)
        )
        stats = []
        for code, total, ng, reviewed, fp in rows.all():
            stats.append({
                "product_code": code or "(未指定)",
                "total": total,
                "ng": int(ng or 0),
                "reviewed": int(reviewed or 0),
                "false_positive": int(fp or 0),
                "false_positive_rate": round(fp / reviewed, 3) if reviewed else None,
            })
    return APIResponse(data={"stats": stats}).model_dump()
