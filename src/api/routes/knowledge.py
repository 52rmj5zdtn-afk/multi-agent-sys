"""
knowledge.py — 知识库管理接口
============================
文档上传、列表、搜索测试、删除。
"""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from src.api.deps import get_hybrid_retriever
from src.retrieval.embedding import get_embedding_service
from src.retrieval.chunker import DocumentChunker
from src.api.schemas.response import APIResponse
from src.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/knowledge", tags=["知识库"])


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    doc_type: str = Form("equipment_manual"),
    workshop_id: str = Form("workshop-a"),
    equipment_model: str = Form(""),
):
    """上传文档: PDF/Word/Excel/TXT → 分块 → 嵌入 → 双索引。

    curl -X POST http://localhost:8000/api/v1/knowledge/upload \\
      -F "file=@manual.pdf" -F "doc_type=equipment_manual"
    """
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")

    # 读取文件内容
    content = await file.read()
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "txt"

    # Phase 2: 纯文本/TXT, 后续接 PDF/Word 解析
    text = ""
    if ext in ("txt", "md", "csv"):
        text = content.decode("utf-8", errors="replace")
    else:
        # 简单二进制文件跳过（后续接 unstructured 解析）
        try:
            text = content.decode("utf-8", errors="replace")
        except Exception:
            raise HTTPException(400, f"暂不支持 {ext} 格式，请上传 TXT/MD 文件")

    if not text.strip():
        raise HTTPException(400, "文件内容为空")

    # 保存原始文件到 data/uploads/
    import os, uuid
    from pathlib import Path
    upload_dir = Path(__file__).parent.parent.parent.parent / "data" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    doc_id = f"upload_{uuid.uuid4().hex[:8]}"
    saved_name = f"{doc_id}_{file.filename}"
    saved_path = upload_dir / saved_name
    saved_path.write_bytes(content)

    # 摄入管线
    hybrid = get_hybrid_retriever()
    embed = get_embedding_service()
    chunker = DocumentChunker()

    chunks = chunker.chunk(text=text, title=file.filename, equipment_model=equipment_model)
    if not chunks:
        raise HTTPException(400, "文档分块失败")

    texts = [c.content for c in chunks]
    vecs = await embed.embed_batch(texts)

    await hybrid.bm25.index([
        {"id": f"{doc_id}_chunk_{i}", "content": t, "title": file.filename,
         "equipment_model": equipment_model}
        for i, t in enumerate(texts)
    ])
    await hybrid.dense.insert("mfg_general_knowledge", [
        {"chunk_id": f"{doc_id}_chunk_{i}", "content": t, "embedding": v,
         "workshop_id": workshop_id, "source": "sop", "doc_title": file.filename,
         "equipment_model": equipment_model}
        for i, (t, v) in enumerate(zip(texts, vecs))
    ])

    logger.info(f"文档上传: {file.filename}, {len(chunks)} chunks → {saved_path}")
    return APIResponse(data={
        "doc_id": doc_id, "filename": file.filename,
        "chunks": len(chunks), "doc_type": doc_type,
        "file_path": str(saved_path),
    }).model_dump()


@router.get("/documents")
async def list_documents():
    """列出索引中的文档 (从 BM25 元数据聚合)。"""
    hybrid = get_hybrid_retriever()
    # BM25 的 corpus 中每个 doc 有 title，按 title 去重聚合
    titles = {}
    for doc in hybrid.bm25._corpus:
        t = doc.get("title", "unknown")
        if t not in titles:
            titles[t] = {"title": t, "chunks": 0, "equipment_model": doc.get("equipment_model", "")}
        titles[t]["chunks"] += 1

    items = list(titles.values())
    return APIResponse(data={"items": items, "total": len(items)}).model_dump()


@router.post("/search")
async def search_knowledge(query: str = "", workshop_id: str = "workshop-a", top_k: int = 10):
    """管理后台检索测试。"""
    if not query.strip():
        return APIResponse(data={"results": [], "latency_ms": 0}).model_dump()

    import time
    t0 = time.time()
    hybrid = get_hybrid_retriever()
    results = await hybrid.search(query=query, workshop_id=workshop_id, top_k=top_k)
    latency = (time.time() - t0) * 1000

    items = [{
        "rank": i + 1,
        "content_preview": r["content"][:150],
        "relevance_score": round(r["relevance_score"], 4),
        "source": r.get("source", ""),
    } for i, r in enumerate(results)]

    return APIResponse(data={"results": items, "latency_ms": round(latency, 1)}).model_dump()


@router.delete("/documents/{title}")
async def delete_document(title: str):
    """按标题删除文档 (从 BM25 + Dense 双索引中移除)。"""
    hybrid = get_hybrid_retriever()
    # BM25 删除
    to_delete = [d["id"] for d in hybrid.bm25._corpus if d.get("title") == title]
    if to_delete:
        await hybrid.bm25.delete(to_delete)
    # Dense 删除
    await hybrid.dense.delete("mfg_general_knowledge", to_delete)
    logger.info(f"文档删除: {title}, {len(to_delete)} chunks")
    return APIResponse(data={"deleted": title, "chunks": len(to_delete)}).model_dump()
