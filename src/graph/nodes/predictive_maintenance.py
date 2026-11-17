"""
predictive_maintenance.py — 预测性维护节点（ML 工具型）
=======================================================
角色：对设备传感器时序调用 RUL 预测服务（sidecar :8002），把剩余寿命写入
      state.ml_results["rul"]，供下游 inspection / scheduling 节点做预警分级与维修窗口建议。

工业场景化（PdM 的业务翻译层）:
  RUL 原始循环数对运维人员没有意义，必须结合设备台账（devices 表）翻译:
    - cycles_per_day    → estimated_days（预计剩余天数）
    - rul_warn_cycles   → 设备级预警阈值（覆盖全局默认，关键设备更保守）
    - critical_component → 工单对象（"更换泵浦二极管"而不是"修设备"）
    - health_score(0-100) → 班组长一眼可读的设备健康分

设备识别策略:
  优先匹配设备台账（device_id 或现场名称如"1号激光焊机"出现在 query 中），
  台账为空时回退到 distinct device_telemetry 旧逻辑（演示零配置）。

执行流程:
  1. 识别设备（台账）→ 取最近 N 条遥测（N > 服务端窗口，给服务端留截取余量）
  2. 调 ML 服务 /predict（客户端自带重试/熔断/指标/追踪）
  3. 成功 → 写 rul_predictions 表（含模型版本/健康分/剩余天数，可审计回放）
     RUL < 设备级阈值 → 自动创建预防性维修工单（幂等：同设备同故障码存在未结工单则跳过）
  4. 失败 → ml_results["rul"] = {"error": ...}，下游节点降级

设计原则与 vision_inspection 节点一致：ML 出结论、LLM 出决策；故障不抛出。
"""
import time
from dataclasses import dataclass

from src.core.config import get_settings
from src.core.logging import get_logger
from src.graph.state import AgentState
from src.ml import get_rul_client
from src.ml.rul import RULClient

logger = get_logger(__name__)

# 节点侧预取的遥测条数：覆盖服务端 WINDOW_SIZE(=30) 并留余量，截取逻辑在服务端
TELEMETRY_FETCH_LIMIT = 60


def _health_score(rul_cycles: float, warn_cycles: float) -> float:
    """设备健康分 0-100：RUL ≥ 阈值 → 100；低于阈值线性衰减到 0。"""
    if warn_cycles <= 0:
        return 100.0
    return round(max(0.0, min(1.0, rul_cycles / warn_cycles)) * 100, 1)


@dataclass
class DeviceInfo:
    """设备台账信息（PdM 业务翻译所需的静态档案）。"""

    device_id: str
    name: str = ""
    critical_component: str = ""
    cycles_per_day: float = 24.0
    rul_warn_cycles: int = 50
    workshop_id: str = ""


@dataclass
class PDMNodeContext:
    """依赖注入容器，方便测试替换 client 与设备解析结果。"""

    rul_client: RULClient | None = None
    device_id_override: str | None = None  # 测试/调度任务可强制指定设备

    def __post_init__(self):
        if self.rul_client is None:
            self.rul_client = get_rul_client()


async def _load_registry() -> dict[str, DeviceInfo]:
    """加载设备台账，返回 {device_id: DeviceInfo}。表不存在/库不可用时返回 {}。"""
    try:
        from src.core.database import get_session_factory
        from src.data.models import Device
        from sqlalchemy import select

        factory = get_session_factory()
        async with factory() as db:
            rows = await db.execute(select(Device))
            devices = rows.scalars().all()
        return {
            d.device_id: DeviceInfo(
                device_id=d.device_id, name=d.name or d.device_id,
                critical_component=d.critical_component or "",
                cycles_per_day=d.cycles_per_day or 24.0,
                rul_warn_cycles=d.rul_warn_cycles or 50,
                workshop_id=d.workshop_id or "",
            )
            for d in devices
        }
    except Exception as e:
        logger.warning(f"设备台账读取失败，退回遥测distinct逻辑: {e}")
        return {}


async def _telemetry_device_ids() -> list[str]:
    """从遥测表取 distinct device_id（台账为空时的回退）。"""
    from src.core.database import get_session_factory
    from src.data.models import DeviceTelemetry
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as db:
        rows = await db.execute(select(DeviceTelemetry.device_id).distinct())
        return sorted({r[0] for r in rows.all()})


async def _resolve_device(state: AgentState, ctx: PDMNodeContext) -> tuple[DeviceInfo | None, list[str]]:
    """解析目标设备：优先台账（device_id 或现场名称命中 query），退回遥测 distinct。"""
    if ctx.device_id_override:
        registry = await _load_registry()
        info = registry.get(ctx.device_id_override) or DeviceInfo(device_id=ctx.device_id_override)
        return info, [info.device_id]

    registry = await _load_registry()
    query = (state.user_query or "").lower()

    if registry:
        for dev in registry.values():
            # 别名: device_id / 全名 / 全名括号前的前缀（中文设备名习惯: "激光焊接主机（TRUMPF...）"）
            short_name = dev.name.split("（")[0].split("(")[0].strip()
            aliases = [dev.device_id.lower()] + ([dev.name.lower()] if dev.name else []) \
                + ([short_name.lower()] if short_name else [])
            if any(a and a in query for a in aliases):
                return dev, sorted(registry.keys())
        if len(registry) == 1:
            only = next(iter(registry.values()))
            return only, sorted(registry.keys())
        return None, sorted(registry.keys())

    known = await _telemetry_device_ids()
    if not known:
        return None, []
    for dev_id in known:
        if dev_id.lower() in query:
            return DeviceInfo(device_id=dev_id), known
    if len(known) == 1:
        return DeviceInfo(device_id=known[0]), known
    return None, known


async def _fetch_recent_readings(device_id: str, limit: int = TELEMETRY_FETCH_LIMIT) -> list[dict]:
    """取设备最近 limit 条遥测（时间升序），readings 列为 JSON。"""
    from src.core.database import get_session_factory
    from src.data.models import DeviceTelemetry
    from sqlalchemy import desc, select

    factory = get_session_factory()
    async with factory() as db:
        rows = await db.execute(
            select(DeviceTelemetry)
            .where(DeviceTelemetry.device_id == device_id)
            .order_by(desc(DeviceTelemetry.cycle))
            .limit(limit)
        )
        items = rows.scalars().all()
    return [dict(item.readings or {}) for item in reversed(items)]


async def _persist_prediction(device: DeviceInfo, result: dict,
                              health_score: float, estimated_days: float | None) -> None:
    """写 rul_predictions 表（best-effort）。"""
    try:
        from src.core.database import get_session_factory
        from src.data.models import RULPrediction

        factory = get_session_factory()
        async with factory() as db:
            db.add(RULPrediction(
                device_id=device.device_id,
                rul_cycles=float(result.get("rul_cycles") or 0.0),
                model=result.get("model_used", "unknown"),
                window_size=int(result.get("window_size") or 0),
                n_readings=int(result.get("n_readings_used") or 0),
                padded=bool(result.get("padded")),
                health_score=health_score,
                estimated_days=estimated_days,
            ))
            await db.commit()
    except Exception as e:
        logger.warning(f"rul_predictions 写入失败（不影响主流程）: {e}")


async def _create_pdm_work_order(state: AgentState, device: DeviceInfo,
                                 rul: float, health_score: float, estimated_days: float | None) -> str | None:
    """RUL 低于设备级预警阈值 → 创建预防性维修工单（幂等：同设备同故障码存在未结工单则跳过）。

    工单对象是台账里的关键部件（"更换泵浦二极管"），而不是笼统的"修设备"。
    """
    try:
        from src.core.database import get_session_factory
        from src.data.models import WorkOrder
        from sqlalchemy import select

        warn = device.rul_warn_cycles
        priority = "P1" if rul < warn * 0.4 else "P2"
        mes_event_id = f"PDM-{device.device_id}-RUL"
        component = device.critical_component or "关键部件"

        user = state.user_context
        factory = get_session_factory()
        async with factory() as db:
            existing = await db.execute(
                select(WorkOrder).where(
                    WorkOrder.equipment_id == device.device_id,
                    WorkOrder.fault_code == "PREDICT_RUL",
                    WorkOrder.status.in_(["pending", "created", "assigned", "in_progress"]),
                )
            )
            if existing.scalars().first():
                logger.info(f"设备 {device.device_id} 已存在未结的预测性维修工单，跳过")
                return mes_event_id
            wo = WorkOrder(
                mes_event_id=mes_event_id,
                equipment_id=device.device_id,
                fault_code="PREDICT_RUL",
                fault_desc=(
                    f"{device.name or device.device_id} {component} 健康分 {health_score}/100，"
                    f"预计剩余 {estimated_days} 天（{rul} 循环，设备级阈值 {warn}）。"
                    "建议安排预防性更换，避免非计划停机。"
                ),
                priority=priority,
                status="pending",
                workshop_id=(user.workshop_id if user else "") or device.workshop_id,
                diagnosis="predict_rul model=lstm",
            )
            db.add(wo)
            await db.commit()
            logger.info(f"预测性维修工单已创建: {mes_event_id} (RUL={rul}, priority={priority})")
            return mes_event_id
    except Exception as e:
        logger.warning(f"预测性维修工单创建失败（不影响预测结果）: {e}")
        return None


def create_pdm_node(ctx: PDMNodeContext | None = None):
    """创建预测性维护节点（工厂模式）。"""
    ctx = ctx or PDMNodeContext()

    async def predictive_maintenance_node(state: AgentState) -> dict:
        t0 = time.time()
        settings = get_settings()

        if not settings.ml_enabled:
            return {
                "ml_results": {**state.ml_results, "rul": {"skipped": True, "reason": "ml_enabled=false"}},
                "agent_outputs": {**state.agent_outputs, "predictive_maintenance": "（ML 已禁用，跳过 RUL 预测）"},
            }

        try:
            device, known = await _resolve_device(state, ctx)
        except Exception as e:
            device, known = None, []
            logger.warning(f"设备解析失败（数据库不可用？）: {e}")

        if device is None:
            hint = f"。已登记设备: {', '.join(known)}" if known else "（尚未登记任何设备）"
            return {
                "ml_results": {**state.ml_results, "rul": {"error": f"未能识别要预测的设备{hint}"}},
                "agent_outputs": {**state.agent_outputs, "predictive_maintenance": f"未能识别要预测的设备{hint}"},
                "errors": state.errors + ["predictive_maintenance: 未能识别设备"],
            }

        try:
            readings = await _fetch_recent_readings(device.device_id)
        except Exception as e:
            return {
                "ml_results": {**state.ml_results, "rul": {"error": f"遥测读取失败: {e}"}},
                "agent_outputs": {**state.agent_outputs, "predictive_maintenance": f"（遥测读取失败: {e}）"},
                "errors": state.errors + [f"predictive_maintenance: 遥测读取失败 {e}"],
            }
        if not readings:
            return {
                "ml_results": {**state.ml_results, "rul": {"error": f"设备 {device.device_id} 暂无遥测数据"}} ,
                "agent_outputs": {**state.agent_outputs, "predictive_maintenance": f"设备 {device.device_id} 暂无遥测数据"},
                "errors": state.errors + [f"predictive_maintenance: 设备 {device.device_id} 无遥测"],
            }

        logger.info(f"PDM Node: 设备 {device.device_id}({device.name})，取最近 {len(readings)} 条遥测")

        try:
            result = await ctx.rul_client.predict(device.device_id, readings)
        except Exception as e:
            logger.warning(f"RUL 预测服务不可用，降级: {e}")
            return {
                "ml_results": {**state.ml_results, "rul": {"error": str(e), "device_id": device.device_id}},
                "agent_outputs": {**state.agent_outputs, "predictive_maintenance": f"（RUL 服务不可用，已降级: {e}）"},
                "errors": state.errors + [f"predictive_maintenance: {e}"],
            }

        # ── 业务翻译：RUL → 健康分 / 剩余天数 ──
        rul = float(result.get("rul_cycles") or 0.0)
        warn = device.rul_warn_cycles
        health = _health_score(rul, warn)
        est_days = round(rul / device.cycles_per_day, 1) if device.cycles_per_day > 0 else None

        result = {**result, "device_id": device.device_id, "device_name": device.name,
                  "critical_component": device.critical_component,
                  "health_score": health, "estimated_days": est_days,
                  "warn_cycles": warn}
        await _persist_prediction(device, result, health, est_days)

        work_order_id = None
        if rul < warn:
            work_order_id = await _create_pdm_work_order(state, device, rul, health, est_days)
        result["work_order_id"] = work_order_id

        padded_note = "（窗口不足已边缘补齐，置信度降低）" if result.get("padded") else ""
        component = f"（关键部件: {device.critical_component}）" if device.critical_component else ""
        days_line = f"- 预计剩余: {est_days} 天（按每天 {device.cycles_per_day:g} 循环折算）\n" if est_days is not None else ""
        summary = (
            f"- 设备: {device.name or device.device_id}{component}\n"
            f"- 设备健康分: {health}/100\n"
            f"- 剩余寿命(RUL): {rul} 循环{padded_note}\n"
            f"{days_line}"
            f"- 预测模型: {result.get('model_used')}（窗口 {result.get('window_size')}，特征 {result.get('n_features')} 维）\n"
            f"- 预警工单: {work_order_id or '未触发（高于设备级阈值 ' + str(warn) + ' 循环）'}"
        )

        latency = (time.time() - t0) * 1000
        logger.info(f"PDM Node 完成: device={device.device_id}, RUL={rul}, health={health}, latency={latency:.0f}ms")

        return {
            "ml_results": {**state.ml_results, "rul": result},
            "agent_outputs": {**state.agent_outputs, "predictive_maintenance": summary},
        }

    return predictive_maintenance_node
