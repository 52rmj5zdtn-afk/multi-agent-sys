"""
router.py — Router Node (意图路由)
===================================
角色：分析用户查询，决定调用哪些 Agent。

路由策略 (Phase 1 简化):
- Phase 1 MVP 只有 Knowledge Agent
- 所有查询都路由到 knowledge
- Phase 2 加入故障诊断等 Agent 后，这里做多 Agent 路由

查询类型分类:
- 复用 HybridRetriever.classify_query() 做查询分类
- Phase 2 用 LLM 做更细粒度的意图识别

使用方式 (LangGraph):
    graph.add_node("router", router_node)
    graph.add_conditional_edges("router", route_after_router, {
        "knowledge": "knowledge",
        "END": END,  # 无匹配 Agent
    })

注意事项:
- Router 不调 LLM (Phase 1)，延迟 < 2ms
- Phase 2 引入 LLM 意图分类时注意成本和延迟
- 查询分类结果写入 state.query_type，供检索层动态调整 RRF 权重
"""

from src.graph.state import AgentState
from src.retrieval.hybrid import HybridRetriever
from src.core.logging import get_logger

logger = get_logger(__name__)


# Phase 1: 所有查询都路由到 knowledge agent
_PHASE1_AGENTS = ["knowledge"]


async def router_node(state: AgentState) -> dict:
    """Router Node 执行函数。

    返回: state 增量更新 dict
          routed_agents: ["knowledge"]
          query_type: "exact" / "mixed" / "semantic"

    示例:
        state.user_query = "海天MA1200 料筒温度异常怎么处理"
        result = await router_node(state)
        # result = {"routed_agents": ["knowledge"], "query_type": "mixed"}

    路由逻辑:
    Phase 1: 全部 → knowledge
    Phase 2: 故障关键词 → diagnosis + knowledge, 质量关键词 → quality + knowledge, etc.
    """
    query = state.user_query

    # Phase 1: 全部路由到 knowledge
    agents = _PHASE1_AGENTS

    # 查询类型分类 (用于检索层调整 RRF 权重)
    # 这里用正则快速分类，不需要 HybridRetriever 实例
    import re
    alarm_pattern = re.compile(r'\b[A-Z]{2,}-\w{3,}\b')
    model_pattern = re.compile(r'([A-Z]{2,}\d{2,}|[A-Z]+-[A-Z]+\d+)|\b[A-Z]{2,}\d{3,}\b')

    has_alarm = bool(alarm_pattern.search(query))
    has_model = bool(model_pattern.search(query))
    fault_kw = ["异常", "故障", "报警", "怎么", "处理", "维修", "排查", "原因"]

    if has_alarm:
        query_type = "exact"
    elif has_model and any(kw in query for kw in fault_kw):
        query_type = "mixed"
    elif has_model:
        query_type = "exact"
    else:
        query_type = "semantic"

    logger.info(f"Router: query_type={query_type}, agents={agents}")

    return {
        "routed_agents": agents,
        "query_type": query_type,
    }
