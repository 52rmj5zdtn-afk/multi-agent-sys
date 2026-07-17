"""
admin.py — 管理接口
===================
种子数据加载等运维操作。
"""
from fastapi import APIRouter

from src.api.deps import get_hybrid_retriever
from src.retrieval.embedding import get_embedding_service
from src.retrieval.chunker import DocumentChunker
from src.api.schemas.response import APIResponse
from src.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["管理"])


@router.post("/seed")
async def load_seed():
    """POST /api/v1/admin/seed — 加载种子文档到检索器。

    每次调用会追加 5 份制造文档到 BM25 + Dense 索引。
    返回加载数量和索引状态。
    """
    from data.seeds.seed_data import SEED_DOCUMENTS

    hybrid = get_hybrid_retriever()
    embed_svc = get_embedding_service()
    chunker = DocumentChunker()

    loaded = 0
    for doc in SEED_DOCUMENTS:
        chunks = chunker.chunk(
            text=doc.content, title=doc.title,
            equipment_model=doc.equipment_model,
        )
        if not chunks:
            continue

        texts = [c.content for c in chunks]
        vecs = await embed_svc.embed_batch(texts)

        await hybrid.bm25.index([
            {"id": f"{doc.doc_id}_chunk_{i}", "content": t,
             "title": doc.title, "equipment_model": doc.equipment_model}
            for i, t in enumerate(texts)
        ])
        await hybrid.dense.insert("mfg_general_knowledge", [
            {"chunk_id": f"{doc.doc_id}_chunk_{i}", "content": t,
             "embedding": v, "workshop_id": doc.workshop_id,
             "source": "sop", "doc_title": doc.title,
             "equipment_model": doc.equipment_model}
            for i, (t, v) in enumerate(zip(texts, vecs))
        ])
        loaded += 1

    logger.info(f"种子数据: {loaded}/{len(SEED_DOCUMENTS)} 份, "
                f"BM25={hybrid.bm25.doc_count}, Dense={hybrid.dense.doc_count('mfg_general_knowledge')}")

    return APIResponse(data={
        "loaded": loaded,
        "bm25_chunks": hybrid.bm25.doc_count,
        "dense_chunks": hybrid.dense.doc_count("mfg_general_knowledge"),
    }).model_dump()
