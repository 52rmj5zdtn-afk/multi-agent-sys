"""inspection.py — 巡检预警 Agent 节点。分析设备状态和报警趋势。"""

from src.graph.state import AgentState
from src.model.fallback import fallback_chain
from src.model.router import TaskComplexity
from src.core.logging import get_logger

logger = get_logger(__name__)

INSPECTION_PROMPT = """你是工厂设备巡检预警专家。分析设备运行状况，给出预警等级和巡检建议。

## 核心规则
1. 若提供了"RUL 剩余寿命预测"，以设备健康分为预警分级核心依据：
   健康分 < 60 → 红色预警（建议尽快安排维修窗口）；
   60-80 → 黄色关注（加密巡检频次，备件准备）；
   ≥ 80 → 正常（例行巡检）。
   窗口不足(padded)的预测需标注置信度降低。
2. 结合文档参考中的点检标准给出可执行的巡检项。

## 回答格式
### 当前状态
- 整体评估: ...
### 预警清单
| 优先级 | 设备 | 异常指标 | 建议 |
### 巡检建议
- 重点区域: ...
- 建议频次: ...
"""


def _format_rul_section(ml_results: dict) -> str:
    """把 RUL 预测结果格式化为 prompt 上下文段落（无结果/出错时返回空串）。

    业务化字段（健康分/剩余天数/关键部件）来自设备台账翻译，巡检分级优先依据健康分：
    <60 红色预警，60-80 黄色关注，80+ 正常。
    """
    rul = (ml_results or {}).get("rul") or {}
    if not rul or rul.get("error"):
        return ""
    padded_note = "（窗口不足已补齐，置信度降低）" if rul.get("padded") else ""
    health = rul.get("health_score")
    health_line = f"- 设备健康分: {health}/100\n" if health is not None else ""
    days = rul.get("estimated_days")
    days_line = f"- 预计剩余: {days} 天\n" if days is not None else ""
    component = rul.get("critical_component")
    component_line = f"- 关键部件: {component}\n" if component else ""
    lines = [
        "## RUL 剩余寿命预测（AI 时序模型 + 设备台账）",
        f"- 设备: {rul.get('device_name') or rul.get('device_id', '未知')}",
        component_line.rstrip("\n") if component_line else None,
        health_line.rstrip("\n") if health_line else None,
        f"- 剩余寿命: {rul.get('rul_cycles')} 循环{padded_note}",
        days_line.rstrip("\n") if days_line else None,
        f"- 预测模型: {rul.get('model_used')}（窗口 {rul.get('window_size')}）",
    ]
    if rul.get("work_order_id"):
        lines.append(f"- 已自动创建预防性维修工单: {rul['work_order_id']}")
    return "\n".join(x for x in lines if x)


async def inspection_node(state: AgentState) -> dict:
    query = state.user_query
    context = state.retrieved_context
    rul_section = _format_rul_section(state.ml_results)
    logger.info(f"Inspection Node: query='{query[:60]}...', rul={'有' if rul_section else '无'}")

    user_msg = f"## 设备数据\n{context}"
    if rul_section:
        user_msg += f"\n\n{rul_section}"
    user_msg += f"\n\n## 查询\n{query}"

    messages = [
        {"role": "system", "content": INSPECTION_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    try:
        result = await fallback_chain.chat_with_fallback(
            messages=messages, complexity=TaskComplexity.COMPLEX, query=query,
        )
    except Exception as e:
        return {"agent_outputs": {**state.agent_outputs, "inspection": ""}, "errors": state.errors + [f"inspection: {e}"]}

    return {"agent_outputs": {**state.agent_outputs, "inspection": result.content}}
