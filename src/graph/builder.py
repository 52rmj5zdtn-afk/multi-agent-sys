"""
builder.py — LangGraph 工作流构建器
====================================
角色：将所有 Node 和 Edge 组装成完整的 LangGraph StateGraph。

Phase 1 工作流:
  START → guard → router → knowledge → aggregate → memory → END

条件跳转:
  guard:
    → injection_blocked=true → END (直接返回拦截信息)
    → injection_blocked=false → router

  router:
    → 所有查询 → knowledge (Phase 1)

使用方式:
    from src.graph.builder import build_graph

    graph = build_graph(hybrid_retriever, session_memory, compressor)
    compiled_graph = graph.compile()

    # 执行
    result = await compiled_graph.ainvoke(
        AgentState(user_query="料筒温度异常", session_id="abc")
    )
    print(result["final_answer"])

注意事项:
- graph.compile() 返回的 CompiledGraph 是多线程安全的
- 每次调用 ainvoke 传入新的初始 state
- LangGraph 自动管理 state 的合并和传递
"""

from langgraph.graph import StateGraph, END, START

from src.graph.state import AgentState
from src.graph.checkpoint import get_checkpoint_manager
from src.graph.nodes.guard import guard_node
from src.graph.nodes.router import router_node
from src.graph.nodes.knowledge import create_knowledge_node, KnowledgeNodeContext
from src.graph.nodes.aggregate import aggregate_node
from src.graph.nodes.memory_node import memory_node
from src.graph.edges import route_after_guard, route_after_router
from src.retrieval.hybrid import HybridRetriever
from src.memory.session import SessionMemory
from src.memory.compressor import ContextCompressor
from src.core.logging import get_logger

logger = get_logger(__name__)


def build_graph(
    hybrid_retriever: HybridRetriever,
    session_memory: SessionMemory | None = None,
    compressor: ContextCompressor | None = None,
) -> StateGraph:
    """构建 Phase 1 LangGraph 工作流。

    参数:
        hybrid_retriever: 混合检索器 (必需)
        session_memory: 会话记忆 (可选，默认全局单例)
        compressor: 上下文压缩器 (可选，默认全局单例)

    返回: 编译后的 CompiledGraph

    示例:
        from src.retrieval.bm25 import BM25Retriever
        from src.retrieval.dense import DenseRetriever
        from src.retrieval.reranker import Reranker
        from src.retrieval.hybrid import HybridRetriever

        bm25 = BM25Retriever()
        dense = DenseRetriever()
        reranker = Reranker()
        hybrid = HybridRetriever(bm25, dense, reranker)

        graph = build_graph(hybrid)
        compiled = graph.compile()

        # 执行查询
        state = AgentState(
            user_query="注塑机料筒温度异常怎么处理",
            session_id="demo_session",
        )
        result = await compiled.ainvoke(state)
        print(result["final_answer"])

    图结构:
        START
          │
          ▼
        guard ──(blocked)──► END
          │(pass)
          ▼
        router
          │
          ▼
        knowledge
          │
          ▼
        aggregate
          │
          ▼
        memory
          │
          ▼
         END
    """
    # 创建 Knowledge Node 上下文
    ctx = KnowledgeNodeContext(
        hybrid_retriever=hybrid_retriever,
        session_memory=session_memory,
        compressor=compressor,
    )
    knowledge_fn = create_knowledge_node(ctx)

    # 构建图
    graph = StateGraph(AgentState)

    # 添加节点
    graph.add_node("guard", guard_node)
    graph.add_node("router", router_node)
    graph.add_node("knowledge", knowledge_fn)
    graph.add_node("aggregate", aggregate_node)
    graph.add_node("memory", memory_node)

    # 添加边
    graph.add_edge(START, "guard")

    # guard → router (正常) / END (注入拦截)
    graph.add_conditional_edges(
        "guard",
        route_after_guard,
        {
            "router": "router",
            "__end__": END,
        },
    )

    # router → knowledge (Phase 1)
    graph.add_edge("router", "knowledge")

    # knowledge → aggregate
    graph.add_edge("knowledge", "aggregate")

    # aggregate → memory
    graph.add_edge("aggregate", "memory")

    # memory → END
    graph.add_edge("memory", END)

    logger.info("LangGraph 工作流构建完成: guard → router → knowledge → aggregate → memory")
    return graph
