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
    -d '{"message": "注塑机怎么换模具", "workshop_id": "workshop-a"}' \
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
from src.security.rbac import DEMO_USERS
from src.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/conversation", tags=["对话"])


async def _stream_chat(request: ChatRequest) -> AsyncIterator[dict]:
    """生成 SSE 事件流。

    流程:
    1. 构建 AgentState
    2. 调用 LangGraph
    3. 将回答切分成 token 级 SSE 事件流 (模拟流式)
    """
    t0 = time.time()
    session_id = request.session_id or f"session_{uuid.uuid4().hex[:12]}"

    # 构建初始 state
    user = DEMO_USERS.get(request.user_id or "worker_zhang")
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
    final_answer = result.get("final_answer", "系统未能获取有效回答")
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
async def chat(request: ChatRequest):
    """POST /api/v1/conversation/chat

    发送对话消息，返回 SSE 流式响应。

    示例请求:
        {"message": "注塑机料筒温度异常怎么处理", "workshop_id": "workshop-a"}

    SSE 事件:
        event: message  → {"type": "text", "content": "根据SOP-HA-12, "}
        event: citation → {"chunk_id": "...", "doc_title": "..."}
        event: done     → {"session_id": "abc", "model_used": "deepseek", ...}
    """
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    return EventSourceResponse(_stream_chat(request))


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
    """DELETE /api/v1/conversation/{session_id}

    删除指定会话 (清除记忆 + Checkpoint)。
    """
    memory = get_session_memory()
    await memory.delete_session(session_id)

    from src.graph.checkpoint import get_checkpoint_manager
    ckpt = get_checkpoint_manager()
    await ckpt.delete_session(session_id)

    return APIResponse(message=f"会话 {session_id} 已删除").model_dump()
