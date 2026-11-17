"""
admin.py — 管理接口
===================
种子数据加载等运维操作。
"""
import os
from pathlib import Path

from fastapi import APIRouter

from src.api.deps import get_hybrid_retriever
from src.retrieval.embedding import get_embedding_service
from src.retrieval.chunker import DocumentChunker
from src.retrieval.ingestion import IngestionPipeline, IngestDocument
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

    # 写入 documents 元数据表
    from datetime import datetime as dt
    _now = lambda: dt.now().replace(microsecond=0)
    doc_count = 0
    try:
        from src.core.database import get_session_factory
        from src.data.models import Document
        from sqlalchemy import select
        factory = get_session_factory()
        async with factory() as session:
            for doc in SEED_DOCUMENTS:
                existing = (await session.execute(select(Document).where(Document.doc_id == (doc.doc_id or "")))).scalar_one_or_none()
                if not existing:
                    session.add(Document(
                        doc_id=doc.doc_id or "", title=doc.title, doc_type=doc.doc_type,
                        workshop_id=doc.workshop_id, equipment_model=doc.equipment_model,
                        classification=doc.classification, chunk_count=0,
                        ingested_at=_now(), status="active",
                    ))
                    doc_count += 1
            await session.commit()
    except Exception as e:
        logger.warning(f"文档元数据写入跳过: {e}")

    logger.info(f"种子数据: {loaded}/{len(SEED_DOCUMENTS)} 份, BM25={hybrid.bm25.doc_count}, Dense={hybrid.dense.doc_count('mfg_general_knowledge')}, 元数据={doc_count}")

    # 自动恢复上传文档到内存索引（从数据库 + 磁盘文件）
    upload_loaded = 0
    try:
        from src.core.database import get_session_factory
        from src.data.models import Document
        from sqlalchemy import select
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(Document).where(
                    Document.status == "active",
                    Document.file_path != "",
                )
            )
            uploaded_docs = result.scalars().all()

        for udoc in uploaded_docs:
            file_path = udoc.file_path
            if not file_path or not Path(file_path).exists():
                continue

            # 使用 IngestionPipeline 解析（支持 PDF/Word/Excel/TXT）
            pipeline = IngestionPipeline()
            ingest_doc = IngestDocument(
                title=udoc.title,
                content="",
                doc_type=udoc.doc_type,
                doc_id=udoc.doc_id,
                workshop_id=udoc.workshop_id,
                equipment_model=udoc.equipment_model,
                metadata={"file_path": file_path},
            )
            text = await pipeline._parse_content(ingest_doc)
            if not text.strip():
                continue

            chunks = chunker.chunk(text=text, title=udoc.title, equipment_model=udoc.equipment_model)
            if not chunks:
                continue

            texts = [c.content for c in chunks]
            vecs = await embed_svc.embed_batch(texts)

            await hybrid.bm25.index([
                {"id": f"{udoc.doc_id}_chunk_{i}", "content": t,
                 "title": udoc.title, "equipment_model": udoc.equipment_model}
                for i, t in enumerate(texts)
            ])
            await hybrid.dense.insert("mfg_general_knowledge", [
                {"chunk_id": f"{udoc.doc_id}_chunk_{i}", "content": t,
                 "embedding": v, "workshop_id": udoc.workshop_id,
                 "source": "sop", "doc_title": udoc.title,
                 "equipment_model": udoc.equipment_model}
                for i, (t, v) in enumerate(zip(texts, vecs))
            ])
            upload_loaded += 1

        logger.info(f"上传文档恢复: {upload_loaded} 份, BM25={hybrid.bm25.doc_count}, Dense={hybrid.dense.doc_count('mfg_general_knowledge')}")
    except Exception as e:
        logger.warning(f"上传文档恢复跳过: {e}")

    # Phase 3: 种子用户写入数据库 (bcrypt 密码哈希)
    try:
        import bcrypt
        from src.core.database import get_session_factory
        from src.data.models import User
        from src.security.rbac import DEMO_USERS
        factory = get_session_factory()
        async with factory() as session:
            for uid, ctx in DEMO_USERS.items():
                existing = await session.get(User, uid)
                if existing is None:
                    session.add(User(
                        id=uid, username=uid, display_name=ctx.display_name,
                        role=ctx.role.value, workshop_id=ctx.workshop_id,
                        password_hash=bcrypt.hashpw(
                            os.environ.get("DEMO_PASSWORD", "demo123").encode(), bcrypt.gensalt()
                        ).decode(),
                    ))
            await session.commit()
        logger.info(f"种子用户: {len(DEMO_USERS)} 人 (bcrypt)")
    except Exception as e:
        logger.warning(f"种子用户跳过: {e}")

    # Phase 3: 也加载种子案例到长期记忆
    case_count = 0
    try:
        from src.memory.persistent import get_long_term_memory, CaseMemory
        ltm = get_long_term_memory()
        for case_data in SEED_CASES:
            try:
                await ltm.save_case(CaseMemory(**case_data))
                case_count += 1
            except Exception:
                pass  # 已存在则跳过
        logger.info(f"种子案例: {case_count} 条")
    except Exception as e:
        logger.warning(f"种子案例加载跳过: {e}")

    return APIResponse(data={
        "loaded": loaded,
        "bm25_chunks": hybrid.bm25.doc_count,
        "dense_chunks": hybrid.dense.doc_count("mfg_general_knowledge"),
        "case_count": case_count,
    }).model_dump()


SEED_CASES = [
    {
        "case_id": "case_seed_001",
        "query": "激光焊机功率衰减报警，报警LW-E-0021",
        "answer_summary": "激光功率反馈异常。核对实际功率vs设定功率，拆检保护镜污染烧损则更换，执行振镜校准(Offset_X/Y<5µm)，检查冷却水机进出水温差(正常3-5℃)，试片拉力测试确认恢复。",
        "equipment_model": "TRUMPF-LW-301",
        "fault_code": "LW-E-0021",
        "fault_category": "功率衰减",
        "workshop_id": "workshop-a",
        "verified": True,
    },
    {
        "case_id": "case_seed_002",
        "query": "焊缝气孔多，怎么解决",
        "answer_summary": "气孔缺陷。检查保护气纯度(≥99.99%)和流量，检查电芯极柱/汇流排表面油污水分，焊接速度降10%试焊。处理后首件目视+3D检测验证。",
        "equipment_model": "TRUMPF-LW-301",
        "fault_code": "",
        "fault_category": "质量",
        "workshop_id": "workshop-a",
        "verified": True,
    },
    {
        "case_id": "case_seed_003",
        "query": "激光焊机焊缝熔宽变窄，功率输出不足",
        "answer_summary": "激光功率衰减。先核对IO_HMI.Laser接口实际功率vs设定，拆检保护镜/聚焦镜污染烧损则更换，执行振镜校准，检查泵浦累计出光时间(阈值40000h)。试片拉力测试确认功率恢复。",
        "equipment_model": "TRUMPF-LW-301",
        "fault_code": "LW-E-0023",
        "fault_category": "功率衰减",
        "workshop_id": "workshop-a",
        "verified": True,
    },
    {
        "case_id": "case_seed_004",
        "query": "涂胶轨迹偏离涂胶槽超出标准",
        "answer_summary": "视觉定位相机标定漂移导致。重新标定相机(每周例行)，检查来料定位基准是否一致。标定后标准块验证偏差<0.05mm，首件全检确认。",
        "equipment_model": "GL-201涂胶机",
        "fault_code": "",
        "fault_category": "质量",
        "workshop_id": "workshop-a",
        "verified": True,
    },
    {
        "case_id": "case_seed_005",
        "query": "冷却水温过高，激光头温度报警",
        "answer_summary": "冷却水机滤网堵塞或压缩机效率下降。检查水温是否>28℃，清洗冷凝器滤网(每周例行)，检查环境通风。水温恢复后观察激光头温度和焦点漂移情况。",
        "equipment_model": "TRUMPF-LW-301",
        "fault_code": "LW-E-0032",
        "fault_category": "冷却",
        "workshop_id": "workshop-a",
        "verified": False,
    },

    # B车间案例
    {
        "case_id": "case_seed_b01",
        "query": "电芯堆叠机压装力曲线突变报警",
        "answer_summary": "来料电芯厚度不一致或压头磨损。抽检电芯厚度公差，检查压装头磨损量，检查压力传感器标定。来料问题则反馈IQC，压头磨损则更换。",
        "equipment_model": "ST-201堆叠机",
        "fault_code": "ST-E-9001",
        "fault_category": "质量",
        "workshop_id": "workshop-b",
        "verified": True,
    },
    {
        "case_id": "case_seed_b02",
        "query": "装配线螺丝锁付扭矩报警AB-E-002",
        "answer_summary": "扭矩设置偏低或螺丝批头磨损。检查扭矩设置值是否正确，更换磨损的螺丝批头，清理螺丝供料器轨道。重新校准后试锁5颗螺丝确认。",
        "equipment_model": "装配线-AB200",
        "fault_code": "AB-E-002",
        "fault_category": "机械",
        "workshop_id": "workshop-b",
        "verified": True,
    },
]


# Phase 3: 案例审核接口
@router.get("/cases/pending")
async def list_pending_cases():
    """GET /api/v1/admin/cases/pending — 列出待审核案例。"""
    from src.memory.persistent import get_long_term_memory
    ltm = get_long_term_memory()
    cases = await ltm.get_pending_cases()
    return APIResponse(data={"items": cases, "total": len(cases)}).model_dump()


@router.post("/cases/{case_id}/approve")
async def approve_case(case_id: str):
    """POST /api/v1/admin/cases/{case_id}/approve — 审核通过案例。"""
    from src.memory.persistent import get_long_term_memory
    ltm = get_long_term_memory()
    ok = await ltm.approve_case(case_id)
    return APIResponse(data={"case_id": case_id, "approved": ok}).model_dump()
