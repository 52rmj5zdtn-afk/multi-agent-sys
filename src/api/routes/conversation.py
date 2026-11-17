"""
conversation.py — 对话接口
==========================
核心用户交互接口:
- POST /chat: SSE 流式对话
- GET /history: 获取历史对话
- DELETE /{session_id}: 删除会话

流式输出 (SSE):
Content-Type: text/event-stream
事件: message (文本), citation (引用), error (错误), done (完成)

使用示例:
  curl -X POST http://localhost:8000/api/v1/conversation/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "激光焊机怎么换工装", "workshop_id": "workshop-a"}' \
    --no-buffer
"""

import json
import time
import uuid
import asyncio
from typing import AsyncIterator

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from src.api.schemas.request import ChatRequest
from src.api.schemas.response import (
    APIResponse, ChatResponse, ConversationHistoryResponse, ConversationMessage,
)
from src.api.deps import get_compiled_graph, get_session_memory
from src.graph.state import AgentState
from src.api.middleware.auth import get_current_user
from src.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/conversation", tags=["对话"])


async def _stream_chat(request: ChatRequest, fastapi_req: Request) -> AsyncIterator[dict]:
    """生成 SSE 事件流。"""
    t0 = time.time()
    session_id = request.session_id or f"session_{uuid.uuid4().hex[:12]}"

    # 构建初始 state
    user = await get_current_user(fastapi_req)
    if user is None:
        raise HTTPException(401, "请先登录")
    state = AgentState(
        user_query=request.message,
        user_context=user,
        session_id=session_id,
        query_type="semantic",
    )

    try:
        # 执行 LangGraph
        graph = get_compiled_graph()
        result = await asyncio.wait_for(
            graph.ainvoke(state),
            timeout=120.0,  # 2分钟超时
        )
    except asyncio.TimeoutError:
        yield {"event": "error", "data": json.dumps({"error": "请求超时，请重试"})}
        return
    except Exception as e:
        logger.error(f"Graph 执行异常: {e}", exc_info=True)
        yield {"event": "error", "data": json.dumps({"error": str(e)})}
        return

    # 提取结果
    final_answer = result.get("final_answer") or "系统未能获取有效回答，请稍后重试"
    citations = result.get("citations", [])
    model_used = result.get("model_used", "unknown")
    latency = result.get("latency_ms", (time.time() - t0) * 1000)

    # 流式输出 (逐 token 发送)
    # Phase 1 简化: 按句子切分发送 (没有真正的 LLM token stream)
    current = ""
    for char in final_answer:
        current += char
        if char in "。！？\n":
            yield {
                "event": "message",
                "data": json.dumps({"type": "text", "content": current}, ensure_ascii=False),
            }
            current = ""
            await asyncio.sleep(0.01)  # 模拟流式延迟
    if current:
        yield {
            "event": "message",
            "data": json.dumps({"type": "text", "content": current}, ensure_ascii=False),
        }

    # 发送引用
    for citation in citations:
        yield {
            "event": "citation",
            "data": json.dumps(citation, ensure_ascii=False),
        }

    # 完成
    yield {
        "event": "done",
        "data": json.dumps({
            "session_id": session_id,
            "model_used": model_used,
            "tokens_used": result.get("token_usage", {}).get("completion", 0),
            "latency_ms": latency,
            "fallback_used": result.get("fallback_used", False),
        }, ensure_ascii=False),
    }


@router.post("/chat")
async def chat(request: ChatRequest, fastapi_req: Request):
    """POST /api/v1/conversation/chat

    发送对话消息，返回 SSE 流式响应。

    示例请求:
        {"message": "激光焊机功率衰减报警怎么处理", "workshop_id": "workshop-a"}

    SSE 事件:
        event: message  → {"type": "text", "content": "根据SOP-HA-12, "}
        event: citation → {"chunk_id": "...", "doc_title": "..."}
        event: done     → {"session_id": "abc", "model_used": "deepseek", ...}
    """
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    return EventSourceResponse(_stream_chat(request, fastapi_req))


@router.get("/history", response_model=dict)
async def get_history(session_id: str, page: int = 1, page_size: int = 20):
    """GET /api/v1/conversation/history?session_id=abc

    获取指定会话的历史消息。
    """
    memory = get_session_memory()
    messages = await memory.get_context(session_id, as_llm_format=False)
    total = len(messages)

    # 分页
    start = (page - 1) * page_size
    end = start + page_size
    page_messages = messages[start:end]

    conv_msgs = []
    for m in page_messages:
        conv_msgs.append(ConversationMessage(
            role=m.get("role", "user"),
            content=m.get("content", ""),
            citations=m.get("citations", []),
            created_at=str(m.get("timestamp", "")),
        ))

    return {
        "code": 0,
        "message": "success",
        "data": {
            "session_id": session_id,
            "messages": [m.model_dump() for m in conv_msgs],
            "message_count": total,
        },
    }


@router.delete("/{session_id}")
async def delete_conversation(session_id: str):
    """DELETE /api/v1/conversation/{session_id} — 删除指定会话 (内存 + 数据库 + Checkpoint)。"""
    memory = get_session_memory()
    await memory.delete_session(session_id)

    from src.graph.checkpoint import get_checkpoint_manager
    ckpt = get_checkpoint_manager()
    await ckpt.delete_session(session_id)

    # 同时删除数据库中的消息记录
    try:
        from src.core.database import get_session_factory
        from src.data.models import Conversation
        from sqlalchemy import delete
        factory = get_session_factory()
        async with factory() as db:
            await db.execute(delete(Conversation).where(Conversation.session_id == session_id))
            await db.commit()
    except Exception as e:
        logger.warning(f"数据库会话删除失败: {e}")

    return APIResponse(message=f"会话 {session_id} 已删除").model_dump()


@router.get("/sessions")
async def list_my_sessions(fastapi_req: Request):
    """GET /api/v1/conversation/sessions — 获取当前登录用户的会话列表。"""
    user = await get_current_user(fastapi_req)
    if not user:
        raise HTTPException(401, "请先登录")
    memory = get_session_memory()
    sessions = memory.get_user_sessions(user.user_id)
    return APIResponse(data={"sessions": sessions, "user_id": user.user_id}).model_dump()


@router.post("/cleanup")
async def cleanup_sessions(fastapi_req: Request, keep_recent: int = 10):
    """POST /api/v1/conversation/cleanup — 清理当前用户的旧会话（保留最近N个）。"""
    user = await get_current_user(fastapi_req)
    if not user:
        raise HTTPException(401, "请先登录")
    memory = get_session_memory()
    sessions = memory.get_user_sessions(user.user_id)
    to_delete = [s["session_id"] for s in sessions[keep_recent:]]
    deleted = 0
    for sid in to_delete:
        await memory.delete_session(sid)
        from src.graph.checkpoint import get_checkpoint_manager
        await get_checkpoint_manager().delete_session(sid)
        deleted += 1
    return APIResponse(data={"deleted": deleted, "remaining": len(sessions) - deleted}).model_dump()


@router.post("/image-chat")
async def image_chat(fastapi_req: Request):
    """POST /api/v1/conversation/image-chat — 图片消息（入图：视觉质检 Agent + 多 Agent 图）。

    融合改造：上传的图片作为附件进入 LangGraph（vision_inspection 节点调 ML 检测，
    quality 节点做处置决策），不再绕过图直接调 LLM。OCR 文字作为查询上下文注入，
    对铭牌/报警屏类图片仍有价值。

    multipart/form-data: file + message(可选描述) + workshop_id + session_id
    """
    from fastapi import UploadFile, File, Form
    import uuid, os
    from pathlib import Path as PathLib

    user = await get_current_user(fastapi_req)
    if not user:
        raise HTTPException(401, "请先登录")

    form = await fastapi_req.form()
    file = form.get("file")
    if not file:
        raise HTTPException(400, "请上传图片文件")

    message = form.get("message", "请分析这张图片中的设备信息")
    workshop_id = form.get("workshop_id", "workshop-a")
    session_id = form.get("session_id", f"img_{uuid.uuid4().hex[:8]}")

    # 保存上传的图片
    upload_dir = PathLib(__file__).parent.parent.parent.parent / "data" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    img_name = f"chat_img_{uuid.uuid4().hex[:8]}.jpg"
    img_path = upload_dir / img_name
    content = await file.read()
    img_path.write_bytes(content)

    # OCR 作为查询上下文（失败则忽略）
    from src.integration.ocr import extract_text
    ocr_text = await extract_text(str(img_path))
    has_ocr = ocr_text and "[OCR识别失败" not in ocr_text

    display_msg = message if message and message != "请分析这张图片中的设备信息" else "[图片]"
    query = display_msg
    if has_ocr:
        query = f"{display_msg}\n[图片OCR文字]: {ocr_text}"
    sid = session_id or f"img_{uuid.uuid4().hex[:8]}"

    # 构建带图片附件的 state，进入 LangGraph：
    # router 检测到附件 → 路由 vision_inspection → quality 做处置决策
    state = AgentState(
        user_query=query,
        user_context=user,
        session_id=sid,
        query_type="semantic",
        attachments=[{"type": "image", "path": str(img_path), "url": f"/uploads/{img_name}"}],
    )

    try:
        graph = get_compiled_graph()
        result = await asyncio.wait_for(graph.ainvoke(state), timeout=120.0)
    except asyncio.TimeoutError:
        return APIResponse(code=50000, message="请求超时，请重试").model_dump()
    except Exception as e:
        logger.error(f"图片对话 Graph 执行异常: {e}", exc_info=True)
        return APIResponse(code=50000, message=f"处理失败: {e}").model_dump()

    final_answer = result.get("final_answer") or "系统未能获取有效回答，请稍后重试"
    img_url = f"/uploads/{img_name}"
    # 会话持久化由图内节点完成（knowledge/memory 节点写会话与 DB），
    # 此处不再重复 add_message——与 /chat 行为保持一致，避免历史重复。

    return APIResponse(data={
        "answer": final_answer,
        "session_id": sid,
        "image_url": img_url,
        "ml_results": result.get("ml_results", {}),
    }).model_dump()


@router.get("/session/{session_id}/restore")
async def restore_session(session_id: str, fastapi_req: Request):
    """GET /api/v1/conversation/session/{session_id}/restore — 从数据库恢复会话。"""
    user = await get_current_user(fastapi_req)
    if not user:
        raise HTTPException(401, "请先登录")
    try:
        from src.core.database import get_session_factory
        from src.data.models import Conversation
        from sqlalchemy import select
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(Conversation)
                .where(Conversation.session_id == session_id)
                .where(Conversation.user_id == user.user_id)
                .order_by(Conversation.created_at)
                .limit(40)
            )
            rows = result.scalars().all()
            messages = [{
                "role": r.role, "content": r.content,
                "citations": r.citations, "model": r.model_used,
                "time": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
            } for r in rows]
            return APIResponse(data={"session_id": session_id, "messages": messages}).model_dump()
    except Exception as e:
        return APIResponse(code=50000, message=f"恢复失败: {e}").model_dump()
