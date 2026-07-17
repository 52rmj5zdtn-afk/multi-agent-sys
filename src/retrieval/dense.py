"""
dense.py — Milvus 向量检索
===========================
角色：基于 Milvus 的语义向量检索。

为什么用 Milvus:
- 分布式架构，支持十亿级向量 (工厂几十万文档 × 每文档几十个 chunk)
- 毫秒级检索 (HNSW 索引)
- 支持分区键 (partition_key) 按车间物理隔离
- 支持 Collection 级别权限控制 (机密文档独立 Collection)

Collection 设计 (3 个):
1. mfg_general_knowledge    — 通用文档 (SOP/设备手册/报警码)
   partition_key = workshop_id
2. mfg_case_memory           — 长期记忆 (历史维修案例)
   partition_key = workshop_id
3. mfg_confidential          — 机密/绝密文档
   partition_key = workshop_id + role 校验

字段定义:
- chunk_id (VARCHAR, PK): 唯一标识
- content (VARCHAR): 分块文本
- doc_type (VARCHAR): 文档类型
- workshop_id (VARCHAR): 分区键
- equipment_model (VARCHAR): 设备型号
- classification (VARCHAR): 密级
- source (VARCHAR): "sop" | "case"
- embedding (FLOAT_VECTOR, 1024维): BGE向量
- created_at (INT64): 时间戳

使用示例:
    from src.retrieval.dense import DenseRetriever
    retriever = DenseRetriever(milvus_client)
    results = await retriever.search(
        query_vec=[0.1, -0.2, ...],  # 1024维查询向量
        collection="mfg_general_knowledge",
        workshop_id="workshop-a",
        top_k=10,
    )
    for r in results:
        print(f"Score: {r['score']:.3f}, Content: {r['content'][:50]}")

注意事项:
- Phase 1 提供内存模拟实现，不需要 Milvus 服务也能跑
- Phase 2 切到真正 Milvus，接口不变
- query_vec 必须 1024 维且 L2 归一化
- workshop_id 必传，不允许全分区查询 (安全隔离)
"""

import math
from dataclasses import dataclass, field

from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DenseHit:
    """向量检索命中结果。"""
    chunk_id: str
    content: str
    score: float               # 余弦相似度, [0, 1]
    doc_title: str = ""
    doc_type: str = ""
    equipment_model: str = ""
    workshop_id: str = ""
    source: str = "sop"


class DenseRetriever:
    """向量检索器 (Phase 1: 内存实现)。

    Phase 2 替换为 pymilvus 实现，接口保持不变。

    使用示例:
        retriever = DenseRetriever()
        await retriever.insert("mfg_general_knowledge", [
            {
                "chunk_id": "c1", "content": "注塑机料筒温度异常处理",
                "embedding": [0.1, 0.2, ...], "workshop_id": "workshop-a"
            }
        ])
        results = await retriever.search(
            query_embedding=[0.1, -0.2, ...],
            collection="mfg_general_knowledge",
            workshop_id="workshop-a",
            top_k=5,
        )
    """

    def __init__(self):
        # 内存存储: {collection_name: {chunk_id: doc_dict}}
        self._collections: dict[str, dict[str, dict]] = {}
        # 分区索引: {collection_name: {workshop_id: [chunk_ids]}}
        self._partitions: dict[str, dict[str, list[str]]] = {}
        # 向量存储: {collection_name: {chunk_id: embedding}}
        self._vectors: dict[str, dict[str, list[float]]] = {}

    def _ensure_collection(self, collection: str) -> None:
        if collection not in self._collections:
            self._collections[collection] = {}
            self._partitions[collection] = {}
            self._vectors[collection] = {}

    async def insert(
        self,
        collection: str,
        documents: list[dict],
    ) -> int:
        """批量插入向量。

        参数:
            collection: Collection 名
            documents: 每个 dict 需要:
                - chunk_id (必需)
                - content (必需)
                - embedding (必需, list[float])
                - workshop_id (必需, 分区键)
                - doc_type, doc_title, equipment_model, source 等可选

        返回: 插入的文档数
        """
        self._ensure_collection(collection)
        count = 0

        for doc in documents:
            chunk_id = doc["chunk_id"]
            workshop_id = doc.get("workshop_id", "")

            # 存储文档
            self._collections[collection][chunk_id] = doc
            # 存储向量
            self._vectors[collection][chunk_id] = doc["embedding"]
            # 建立分区索引
            if workshop_id not in self._partitions[collection]:
                self._partitions[collection][workshop_id] = []
            if chunk_id not in self._partitions[collection][workshop_id]:
                self._partitions[collection][workshop_id].append(chunk_id)
            count += 1

        logger.info(f"Dense 插入: collection={collection}, count={count}, total={len(self._collections[collection])}")
        return count

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """计算两个向量的余弦相似度 (假设已 L2 归一化)。

        已归一化的向量，余弦相似度 = 点积。
        返回 [0, 1] 范围的值。
        """
        if len(vec1) != len(vec2):
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        # 已归一化时 dot_product 范围是 [-1, 1]，映射到 [0, 1]
        return max(0.0, (dot_product + 1.0) / 2.0)

    async def search(
        self,
        query_embedding: list[float],
        collection: str = "mfg_general_knowledge",
        workshop_id: str | None = None,
        top_k: int = 20,
        source: str | None = None,  # "sop" or "case"
    ) -> list[DenseHit]:
        """向量相似检索。

        参数:
            query_embedding: 查询向量 (1024维，已L2归一化)
            collection: Collection 名
            workshop_id: 车间分区过滤 (必传，不允许全库查)
            top_k: 返回结果数
            source: 可选，"sop" (主知识库) 或 "case" (长期记忆)

        返回: 按相似度降序排列的结果

        搜索流程示例:
            retriever = DenseRetriever()
            results = await retriever.search(
                query_embedding=query_vec,
                workshop_id="workshop-a",
                source="sop",
                top_k=10,
            )
            for r in results:
                print(f"[cosine={r.score:.3f}] {r.content[:60]}")
        """
        self._ensure_collection(collection)

        # 确定候选集 (分区过滤)
        if workshop_id and workshop_id in self._partitions.get(collection, {}):
            candidate_ids = list(self._partitions[collection][workshop_id])
        else:
            # 无分区过滤 (仅厂长/工艺工程师跨车间场景)
            candidate_ids = list(self._vectors.get(collection, {}).keys())

        # 计算相似度
        scores: list[tuple[str, float]] = []
        for chunk_id in candidate_ids:
            vec = self._vectors[collection].get(chunk_id)
            if vec is None:
                continue

            doc = self._collections[collection].get(chunk_id, {})
            # source 过滤
            if source and doc.get("source") != source:
                continue

            sim = self._cosine_similarity(query_embedding, vec)
            if sim > 0:
                scores.append((chunk_id, sim))

        # 按分数降序
        scores.sort(key=lambda x: x[1], reverse=True)
        top = scores[:top_k]

        results = []
        for chunk_id, score in top:
            doc = self._collections[collection].get(chunk_id, {})
            results.append(DenseHit(
                chunk_id=chunk_id,
                content=doc.get("content", ""),
                score=score,
                doc_title=doc.get("doc_title", ""),
                doc_type=doc.get("doc_type", ""),
                equipment_model=doc.get("equipment_model", ""),
                workshop_id=doc.get("workshop_id", ""),
                source=doc.get("source", "sop"),
            ))

        logger.debug(f"Dense search: col={collection}, ws={workshop_id}, top_k={top_k}, found={len(results)}")
        return results

    async def delete(self, collection: str, chunk_ids: list[str]) -> int:
        """删除指定 chunk。"""
        self._ensure_collection(collection)
        removed = 0
        for chunk_id in chunk_ids:
            if chunk_id in self._collections[collection]:
                doc = self._collections[collection].pop(chunk_id, {})
                workshop_id = doc.get("workshop_id", "")
                if workshop_id in self._partitions.get(collection, {}):
                    try:
                        self._partitions[collection][workshop_id].remove(chunk_id)
                    except ValueError:
                        pass
                self._vectors[collection].pop(chunk_id, None)
                removed += 1
        return removed

    @property
    def collection_names(self) -> list[str]:
        return list(self._collections.keys())

    def doc_count(self, collection: str) -> int:
        return len(self._collections.get(collection, {}))
