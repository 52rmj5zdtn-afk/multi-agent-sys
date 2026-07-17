"""
memory_node.py — Memory Node (记忆持久化)
=========================================
角色：LangGraph 工作流的最后一个节点。负责持久化对话记忆 + 触发上下文压缩。

职责:
1. 保存 Checkpoint → 防止服务重启后流程丢失
2. (Phase 3) 长期记忆评估 → 判断是否需要沉淀故障案例
3. (Phase 3) 知识沉淀触发 → 满足条件时生成案例草稿

Phase 1 简化:
- 仅做 Checkpoint 保存
- 不做长期记忆和知识沉淀

使用方式 (LangGraph):
    graph.add_node("memory", memory_node)
    graph.add_edge("aggregate", "memory")
    graph.add_edge("memory", "output_guard")  # 或 END
"""

from src.graph.state import AgentState
from src.graph.checkpoint import get_checkpoint_manager
from src.core.logging import get_logger

logger = get_logger(__name__)


async def memory_node(state: AgentState) -> dict:
    """Memory Node 执行函数。

    Phase 1: 保存 Checkpoint + 返回空 dict
    Phase 3: 增加长期记忆评估 + 知识沉淀触发

    返回: 空 dict (不修改 state)

    示例:
        result = await memory_node(state)
        # result = {} (checkpoint 已保存到 Redis)
    """
    session_id = state.session_id
    if not session_id:
        logger.warning("Memory Node: session_id 为空，跳过 checkpoint")
        return {}

    # 保存 Checkpoint
    ckpt = get_checkpoint_manager()
    await ckpt.save(session_id, "memory", state)

    # 续期: 如果会话可能还在活跃中
    await ckpt.renew(session_id)

    logger.debug(f"Memory Node: checkpoint 已保存, session={session_id}")
    return {}
