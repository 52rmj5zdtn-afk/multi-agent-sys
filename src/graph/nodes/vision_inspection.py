"""
vision_inspection.py — 视觉质检节点（ML 工具型）
=================================================
角色：对上传的产品图片调用视觉缺陷检测服务（sidecar :8001），把结构化结果写入
      state.ml_results["vision"]，供下游 quality 节点做处置决策（停机/降级/放行）。

执行流程:
  1. 从 state.attachments 取第一张图片（无附件 → 记录原因直接返回）
  2. 调 ML 服务 /inspect（客户端自带重试/熔断/指标）
  3. 成功 → 写 inspection_records 表（可追溯）+ audit_logs
     NG 且异常分数 >= ml_ng_score_threshold → 自动创建视觉工单（确定性动作，不经 LLM）
  4. 失败（服务不可达/熔断）→ ml_results["vision"] = {"error": ...}，
     下游 quality 节点照常工作并注明降级

设计原则:
  - ML 出结论、LLM 出决策：本节点只做检测与留痕，不做处置判断
  - 任何外部故障都不抛出，全部收敛到 state.errors / ml_results，保证图继续执行
  - DB 写入 best-effort（表未建/数据库不可用时仅告警，不影响主流程）
"""
import time
from dataclasses import dataclass

from src.core.config import get_settings
from src.core.logging import get_logger
from src.graph.state import AgentState
from src.ml import get_vision_client
from src.ml.vision import VisionClient

logger = get_logger(__name__)


@dataclass
class VisionNodeContext:
    """依赖注入容器（与 KnowledgeNodeContext 同模式），方便测试替换 client。"""

    vision_client: VisionClient | None = None

    def __post_init__(self):
        if self.vision_client is None:
            self.vision_client = get_vision_client()


def _first_image_attachment(state: AgentState) -> dict | None:
    for att in state.attachments or []:
        if att.get("type") == "image" and att.get("path"):
            return att
    return None


@dataclass
class ProductRoute:
    """质检换型路由：产品 → 模型 + 阈值 + 工位。"""

    model_name: str = "patchcore"
    threshold: float | None = None
    station_id: str = ""
    product_code: str = ""


async def _resolve_product(attachment: dict) -> ProductRoute:
    """按 inspection_products 映射解析检测模型与阈值（换型）。

    优先级：附件 product 字段 → 唯一启用产品 → 默认 patchcore（无阈值覆盖）。
    表不存在/库不可用 → 默认 patchcore。
    """
    try:
        from src.core.database import get_session_factory
        from src.data.models import InspectionProduct
        from sqlalchemy import select

        factory = get_session_factory()
        async with factory() as db:
            rows = await db.execute(select(InspectionProduct).where(InspectionProduct.enabled.is_(True)))
            products = rows.scalars().all()

        by_code = {p.product_code: p for p in products}
        code = attachment.get("product") or ""
        chosen = by_code.get(code)
        if chosen is None and not code and len(products) == 1:
            chosen = products[0]
        if chosen is None:
            logger.info(f"产品 {code or '(未指定)'} 无换型映射，使用默认模型 patchcore")
            return ProductRoute(product_code=code)
        return ProductRoute(
            model_name=chosen.model_name or "patchcore",
            threshold=chosen.threshold,
            station_id=chosen.station_id or "",
            product_code=chosen.product_code,
        )
    except Exception as e:
        logger.warning(f"换型映射读取失败，使用默认模型: {e}")
        return ProductRoute(product_code=attachment.get("product") or "")


async def _persist_inspection(state: AgentState, result: dict, work_order_id: str | None,
                              product: ProductRoute) -> None:
    """写 inspection_records + audit_logs（best-effort）。"""
    try:
        from src.core.database import get_session_factory
        from src.data.models import InspectionRecord

        user = state.user_context
        factory = get_session_factory()
        async with factory() as db:
            db.add(InspectionRecord(
                image_path=result.get("image", ""),
                image_url=(_first_image_attachment(state) or {}).get("url", ""),
                model=result.get("model", "patchcore"),
                product_code=product.product_code,
                station_id=product.station_id,
                defect_detected=bool(result.get("defect_detected")),
                anomaly_score=float(result.get("anomaly_score") or 0.0),
                threshold=result.get("threshold"),
                heatmap_path=result.get("anomaly_map_path", ""),
                work_order_id=work_order_id,
                workshop_id=user.workshop_id if user else "",
                created_by=user.user_id if user else "",
            ))
            await db.commit()
    except Exception as e:
        logger.warning(f"inspection_records 写入失败（不影响主流程）: {e}")

    try:
        from src.core.database import get_session_factory
        from src.data.models import AuditLog

        factory = get_session_factory()
        async with factory() as db:
            db.add(AuditLog(
                event_type="ml_inspection",
                agent_id="vision_inspection",
                tool_name="defect_inspection",
                detail={
                    "defect_detected": bool(result.get("defect_detected")),
                    "anomaly_score": result.get("anomaly_score"),
                    "work_order_id": work_order_id,
                },
            ))
            await db.commit()
    except Exception as e:
        logger.warning(f"审计日志写入失败: {e}")


async def _create_ng_work_order(state: AgentState, result: dict) -> str | None:
    """NG 超阈值 → 创建视觉质检工单（幂等：同一 mes_event_id 不会重复创建）。"""
    try:
        from src.core.database import get_session_factory
        from src.data.models import WorkOrder
        from sqlalchemy import select

        settings = get_settings()
        score = float(result.get("anomaly_score") or 0.0)
        priority = "P1" if score >= 0.8 else "P2"
        image_name = result.get("image", "unknown")
        mes_event_id = f"VIS-{image_name.rsplit('/', 1)[-1].rsplit('\\\\', 1)[-1]}"

        user = state.user_context
        factory = get_session_factory()
        async with factory() as db:
            existing = await db.execute(
                select(WorkOrder).where(WorkOrder.mes_event_id == mes_event_id)
            )
            if existing.scalar_one_or_none():
                logger.info(f"视觉工单已存在，跳过重复创建: {mes_event_id}")
                return mes_event_id
            wo = WorkOrder(
                mes_event_id=mes_event_id,
                equipment_id="vision-station",
                fault_code="VISUAL_NG",
                fault_desc=(
                    f"AI视觉检测判定NG。异常分数 {score}"
                    f"（阈值 {result.get('threshold') or '模型默认'}），"
                    f"热力图: {result.get('anomaly_map_path', '')}"
                ),
                priority=priority,
                status="pending",
                workshop_id=user.workshop_id if user else "",
                diagnosis=f"defect_inspection model={result.get('model', 'patchcore')}",
            )
            db.add(wo)
            await db.commit()
            logger.info(f"视觉NG工单已创建: {mes_event_id} (priority={priority})")
            return mes_event_id
    except Exception as e:
        logger.warning(f"视觉工单创建失败（不影响检测结果）: {e}")
        return None


def create_vision_node(ctx: VisionNodeContext | None = None):
    """创建视觉质检节点（工厂模式，与 create_knowledge_node 对齐）。"""
    ctx = ctx or VisionNodeContext()

    async def vision_inspection_node(state: AgentState) -> dict:
        t0 = time.time()
        settings = get_settings()

        if not settings.ml_enabled:
            return {
                "ml_results": {**state.ml_results, "vision": {"skipped": True, "reason": "ml_enabled=false"}},
                "agent_outputs": {**state.agent_outputs, "vision_inspection": "（ML 已禁用，跳过视觉检测）"},
            }

        attachment = _first_image_attachment(state)
        if attachment is None:
            return {
                "ml_results": {**state.ml_results, "vision": {"error": "state.attachments 中没有图片附件"}},
                "agent_outputs": {**state.agent_outputs, "vision_inspection": "（无图片附件，跳过视觉检测）"},
                "errors": state.errors + ["vision_inspection: 无图片附件"],
            }

        image_path = attachment["path"]
        product = await _resolve_product(attachment)
        # 检测通道: 产品编码 -3d 结尾 → depth 深度图通道（服务端做几何测量），否则 2D 外观
        tag = "depth" if product.product_code.endswith("-3d") else "2d"
        logger.info(f"Vision Node: 检测图片 {image_path}（产品 {product.product_code or '默认'}, "
                    f"模型 {product.model_name}, 通道 {tag}, 阈值覆盖 {product.threshold}）")

        try:
            result = await ctx.vision_client.inspect(
                image_path, model=product.model_name, threshold=product.threshold, tag=tag,
            )
        except Exception as e:
            logger.warning(f"视觉检测服务不可用，降级: {e}")
            return {
                "ml_results": {**state.ml_results, "vision": {"error": str(e)}},
                "agent_outputs": {**state.agent_outputs, "vision_inspection": f"（视觉服务不可用，已降级: {e}）"},
                "errors": state.errors + [f"vision_inspection: {e}"],
            }

        # 判定 NG 由产品级阈值（或模型默认阈值）完成；工单优先级按分数升级
        work_order_id = None
        if result.get("defect_detected"):
            work_order_id = await _create_ng_work_order(state, result)

        await _persist_inspection(state, result, work_order_id, product)

        verdict = "NG（存在缺陷）" if result.get("defect_detected") else "OK（未见异常）"
        product_line = f"- 产品/工位: {product.product_code or '未指定'}/{product.station_id or '-'}\n" if (product.product_code or product.station_id) else ""
        summary = (
            f"- 检测结论: {verdict}\n"
            f"{product_line}"
            f"- 异常分数: {result.get('anomaly_score')}（判定阈值: {result.get('threshold') or '模型内置'}）\n"
            f"- 像素级热力图: {result.get('anomaly_map_path', '')}\n"
            f"- 自动工单: {work_order_id or '未触发（待质检员复核后可手动发起）'}"
        )
        result = {**result, "work_order_id": work_order_id,
                  "product_code": product.product_code, "station_id": product.station_id}

        latency = (time.time() - t0) * 1000
        logger.info(f"Vision Node 完成: {verdict}, latency={latency:.0f}ms")

        return {
            "ml_results": {**state.ml_results, "vision": result},
            "agent_outputs": {**state.agent_outputs, "vision_inspection": summary},
        }

    return vision_inspection_node
