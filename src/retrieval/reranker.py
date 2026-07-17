"""
reranker.py — Cross-encoder 重排序
===================================
角色：对混合检索的候选结果做精排，用 Cross-encoder 模型计算查询和文档的精确相关性。

为什么要 Re-ranker:
- BM25 只看关键词匹配，可能把"温度"相关文档全排在前面，但"料筒温度"才是用户要的
- Dense (向量) 看的是语义相似度，但可能把"温度异常→天气太热→空调故障"排到前面
- Cross-encoder 把查询和文档同时输入，做逐 token 的深度匹配，精度更高

三段式检索流程:
  Step 1: BM25 (ES)  → 粗召回 50 条  (快速, 关键词精度)
  Step 2: Dense (Milvus) → 粗召回 50 条 (快速, 语义相关性)
  Step 3: RRF 融合 → 合并去重 → 30 条候选
  Step 4: Re-ranker → 精排 → Top-10/20

性能权衡:
- Step 1+2: 毫秒级 (向量检索 + BM25)
- Step 4: 相对慢 (每个 query-doc pair 都要过模型，30 条约 500ms)
- 所以 Re-ranker 只对 RRF 融合后的 Top-30 做精排，不对原始的 100 条全排

模型选择: BGE-Reranker-v2-m3
- 中文 Cross-encoder 精度最高的之一
- 支持处理长文本 (最大 8192 tokens)

使用示例:
    from src.retrieval.reranker import Reranker
    reranker = Reranker()
    query = "注塑机料筒温度异常怎么处理"
    candidates = [
        "步骤1: 检查温控器...   步骤2: 测量传感器电阻...",
        "注塑机每日点检项目包括: 1. 检查料筒加热圈...",
        "冲压模具保养周期表, 每5000次冲压需检查...",
    ]
    ranked = await reranker.rerank(query, candidates, top_k=3)
    for cand in ranked:
        print(f"[{cand['relevance_score']:.3f}] {cand['content'][:60]}")

注意事项:
- Phase 1 提供轻量实现 (基于 BM25 分数模拟，不需要下载模型)
- Phase 2 接入真正的 BGE-Reranker-v2-m3
- Re-ranker 调用频率 = 每次搜索 × 1，是检索链路的性能瓶颈
- 候选集越大越慢: 推荐 20-30 条候选做精排
"""

from dataclasses import dataclass

from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RerankResult:
    chunk_id: str
    content: str
    relevance_score: float   # [0, 1], 越高越相关
    original_rank: int       # 原排名
    source: str = ""         # "bm25" / "dense" / "sop" / "case"


class Reranker:
    """重排序器 (Phase 1: 轻量模拟实现)。

    实际原理: 模拟 Cross-encoder 行为，对混合检索的结果按分数加权。
    Phase 2 替换为 BGE-Reranker-v2-m3 实现。

    示例:
        reranker = Reranker()
        ranked = await reranker.rerank(
            query="料筒温度异常",
            candidates=[
                {"id": "c1", "content": "检查温控器", "score": 0.85, "source": "dense"},
                {"id": "c2", "content": "冲压线操作规程", "score": 0.45, "source": "bm25"},
            ],
            equipment_model="海天MA1200",
        )
        # ranked[0]: 最相关的结果
    """

    def __init__(self):
        self._model_loaded = False

    async def _load_model(self) -> None:
        """Phase 2: 加载 BGE-Reranker-v2-m3 模型。"""
        # TODO Phase 2: 真正加载模型
        # from FlagEmbedding import FlagReranker
        # self.model = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)
        self._model_loaded = True

    async def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int = 10,
        equipment_model: str | None = None,
    ) -> list[RerankResult]:
        """对候选集做精排。

        参数:
            query: 用户查询
            candidates: 候选列表，每个 dict: {id, content, score, source}
            top_k: 返回 Top-K 结果
            equipment_model: 可选，用于型号 boost

        返回: RerankResult 列表，按相关性降序

        排序策略 (Phase 1 轻量版):
        1. 基础分 = 原始检索分数 * 来源权重
           - sop (官方文档): weight=1.2 (优先级最高)
           - verified case (已验证案例): weight=1.0
           - unverified case (待验证案例): weight=0.7
        2. 型号 boost: 候选内容含查询中的设备型号 → score × 1.3
        3. 报警码精确匹配 boost: 候选含查询的报警码 → score × 1.5
        4. 对原始分做归一化，去重后按最终分降序
        """
        if not candidates:
            return []

        # 提取查询中可能的设备型号和报警码
        query_upper = query.upper()
        query_keywords = query.lower().split()

        # 来源权重
        source_weights = {
            "sop": 1.2,
            "verified_case": 1.0,
            "unverified_case": 0.7,
            "dense": 1.0,
            "bm25": 1.0,
        }

        results = []
        seen_contents = set()  # 去重 (相同内容只保留分数最高的)

        for rank, cand in enumerate(candidates):
            content = cand.get("content", "")
            source = cand.get("source", "bm25")
            score = cand.get("score", 0.5)

            # 基础权重
            weight = source_weights.get(source, 1.0)

            # 型号 boost: 候选内容包含用户查询里的设备型号 → +30%
            if equipment_model and equipment_model.lower() in content.lower():
                weight *= 1.3

            # 报警码 boost: 候选含报警码格式 (如 HT-E-0021)
            import re
            alarm_codes = re.findall(r'[A-Z]{2,}-\w{3,}', query_upper)
            for code in alarm_codes:
                if code in content.upper():
                    weight *= 1.5
                    break

            final_score = min(score * weight, 1.0)

            # 去重
            content_fingerprint = content[:100].strip()
            if content_fingerprint in seen_contents:
                continue
            seen_contents.add(content_fingerprint)

            results.append(RerankResult(
                chunk_id=cand.get("id", ""),
                content=content,
                relevance_score=final_score,
                original_rank=rank,
                source=source,
            ))

        # 按相关性分数降序
        results.sort(key=lambda r: (r.relevance_score, -r.original_rank), reverse=True)
        return results[:top_k]
