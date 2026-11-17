"""quality.py — 质量分析 Agent 节点。分析产品缺陷与工艺参数的关联。"""

from src.graph.state import AgentState
from src.model.fallback import fallback_chain
from src.model.router import TaskComplexity
from src.core.logging import get_logger

logger = get_logger(__name__)

QUALITY_PROMPT = """你是制造质量分析专家。根据缺陷描述和设备状况，追溯可能的工艺原因并给出调整建议。

## 核心规则
1. 若提供了"AI视觉检测结果"，以该结果作为缺陷判定的事实依据，你负责处置决策（停机/降级放行/加严抽检）并说明理由。
2. 结合文档参考与诊断结论追溯工艺原因。

## 回答格式
### 缺陷分析
- 缺陷类型: ...
- 可能原因（按概率排序）
### 处置建议（基于 AI 检测结果）
- 判定: 停机 / 降级放行 / 加严抽检
- 理由: ...
### 建议措施
1. ...
### 质检建议
- 是否增加频次: ...
"""


def _format_vision_section(ml_results: dict) -> str:
    """把视觉检测结果格式化为 prompt 上下文段落（无结果/出错时返回空串）。"""
    vision = (ml_results or {}).get("vision") or {}
    if not vision or vision.get("error"):
        return ""
    verdict = "NG（存在缺陷）" if vision.get("defect_detected") else "OK（未见异常）"
    lines = [
        "## AI 视觉检测结果（事实依据）",
        f"- 判定: {verdict}",
        f"- 异常分数: {vision.get('anomaly_score')}（阈值: {vision.get('threshold') or '模型内置'}）",
        f"- 热力图: {vision.get('anomaly_map_path', '')}",
    ]
    if vision.get("work_order_id"):
        lines.append(f"- 已自动创建工单: {vision['work_order_id']}")
    return "\n".join(lines)


async def quality_node(state: AgentState) -> dict:
    query = state.user_query
    context = state.retrieved_context
    diagnosis = state.agent_outputs.get("diagnosis", "")
    vision_section = _format_vision_section(state.ml_results)
    logger.info(f"Quality Node: query='{query[:60]}...', vision={'有' if vision_section else '无'}")

    user_msg = f"## 故障诊断\n{diagnosis}\n\n## 文档参考\n{context}"
    if vision_section:
        user_msg += f"\n\n{vision_section}"
    user_msg += f"\n\n## 用户查询\n{query}"

    messages = [
        {"role": "system", "content": QUALITY_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    try:
        result = await fallback_chain.chat_with_fallback(
            messages=messages, complexity=TaskComplexity.COMPLEX, query=query,
        )
    except Exception as e:
        return {"agent_outputs": {**state.agent_outputs, "quality": ""}, "errors": state.errors + [f"quality: {e}"]}

    return {"agent_outputs": {**state.agent_outputs, "quality": result.content}}
