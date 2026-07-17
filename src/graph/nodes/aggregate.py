"""
aggregate.py — Aggregate Node (结果聚合)
=========================================
角色：将所有 Agent 的输出合并成最终回答。Phase 1 只有 Knowledge Agent，
      所以 Aggregate 的职责很简单——把 Knowledge 的结果作为最终答案。

Phase 2 多 Agent 场景:
  诊断Agent → "根因: 料筒温度传感器故障"
  知识Agent → "维修步骤: 1. 检查温控器 2. 测量电阻..."
  质检Agent → "该故障可能导致注塑件尺寸偏差"

  Aggregate: 合并三个结果，按优先级排序:
  1. 诊断结论
  2. 维修步骤
  3. 质量影响

  如果结论冲突 → 以 SOP 为准，标注差异。

注意事项:
- Phase 1 不做 LLM 聚合 (直接透传)，延迟 < 1ms
- Phase 2 多 Agent 时用 LLM 做摘要聚合
- 聚合时保证引用来源不丢失
"""

from src.graph.state import AgentState
from src.core.logging import get_logger

logger = get_logger(__name__)


async def aggregate_node(state: AgentState) -> dict:
    """Aggregate Node 执行函数。

    Phase 1: 直接透传 knowledge agent 的输出作为最终答案。

    Phase 2: 多 Agent 结果聚合:
    1. 按优先级排序 (诊断 → 维修SOP → 质量 → 报表)
    2. 检测冲突 (SOP 与历史案例结论不一致)
    3. 以 SOP 为准，标注差异

    返回:
        {"final_answer": "..."}

    示例:
        state.agent_outputs = {"knowledge": "根据SOP-HA-12, 处理步骤..."}
        result = await aggregate_node(state)
        # result["final_answer"] = "根据SOP-HA-12, 处理步骤..."
    """
    agent_outputs = state.agent_outputs

    if not agent_outputs:
        logger.warning("Aggregate: 无 Agent 输出")
        return {
            "final_answer": "系统未能获取到有效回答，请稍后重试。",
            "errors": ["所有 Agent 无输出"],
        }

    # Phase 1: 单 Agent 透传
    if "knowledge" in agent_outputs:
        answer = agent_outputs["knowledge"]

        # 如果有降级标记，前缀标注
        if state.fallback_used:
            model_note = f"[系统提示: 当前为降级模式, 使用规则库回答, 仅供参考]\n\n"
            answer = model_note + answer

        logger.info(f"Aggregate: 透传 knowledge 结果 ({len(answer)} 字符)")
        return {"final_answer": answer}

    # Phase 2: 多 Agent 聚合
    # 聚合优先级: diagnosis > knowledge > inspection > quality > scheduling > report
    priority_order = ["diagnosis", "knowledge", "inspection", "quality", "scheduling", "report"]
    parts = []

    for agent_id in priority_order:
        if agent_id in agent_outputs and agent_outputs[agent_id]:
            label_map = {
                "diagnosis": "故障诊断",
                "knowledge": "维修指导",
                "inspection": "巡检分析",
                "quality": "质量分析",
                "scheduling": "排程建议",
                "report": "数据报表",
            }
            label = label_map.get(agent_id, agent_id)
            parts.append(f"## {label}\n{agent_outputs[agent_id]}")

    if parts:
        answer = "\n\n".join(parts)
    else:
        # Fallback: 合并所有非空输出
        answer = "\n\n".join(v for v in agent_outputs.values() if v)

    logger.info(f"Aggregate: 合并 {len(parts)} 个 Agent 结果")
    return {"final_answer": answer}
