"""
session.py — Redis 短期记忆管理
===============================
角色：管理单个会话的对话上下文，让模型能记住"刚才聊了什么"。

为什么需要短期记忆:
- 用户问"这台机器怎么办" → 需要知道上文说的"这台机器"是"HA-003 注塑机"
- 用户追问"那步骤2呢" → 需要知道步骤2指的是上一轮回答的维修步骤

存储结构:
Redis Key: session:{session_id}
类型: LIST (FIFO 队列)
内容: 每轮对话的 JSON 序列化消息
TTL: 2 小时 (会话活跃时自动续期)

窗口策略:
- 保留最近 N 轮对话 (默认 20 条消息，即 10 轮问答)
- 超过 N 轮的旧消息自动从 LIST 尾部弹出 (LTRIM)
- 20 条消息约 4000-8000 tokens，留给 LLM 回答的空间充足

消息格式:
{
    "role": "user" | "assistant" | "system",
    "content": "...",
    "timestamp": "2026-07-17T14:30:00Z",
    "citations": [{"doc_id": "...", "title": "..."}],  // 仅 assistant
    "model": "deepseek-chat",                           // 仅 assistant
}

使用示例:
    from src.memory.session import SessionMemory, get_session_memory

    memory = get_session_memory()

    # 添加一轮对话
    await memory.add_message("session_abc", {"role": "user", "content": "料筒温度异常"})
    await memory.add_message("session_abc", {"role": "assistant", "content": "根据SOP-HA-12..."})

    # 获取上下文 (给 LLM 用)
    context = await memory.get_context("session_abc", max_messages=20)
    # context = [
    #   {"role": "user", "content": "料筒温度异常"},
    #   {"role": "assistant", "content": "根据SOP-HA-12..."}
    # ]

    # 获取最后一条用户消息 (用于快捷指令)
    last_user_msg = await memory.get_last_user_message("session_abc")

注意事项:
- Phase 1 提供内存实现 (不需要 Redis)，Phase 2 切 Redis
- 会话 ID 由 API 层生成，前端传回来维持同一会话
- system prompt 不存这里，在 graph node 里动态注入
- 消息超过 2000 个字符会被压缩 (长日志粘贴场景)
"""

import json
import time
from typing import Optional

from src.core.logging import get_logger

logger = get_logger(__name__)

MAX_MESSAGE_LENGTH = 2000  # 单条消息最长 2000 字符


class SessionMemory:
    """会话记忆管理器 (Phase 1: 内存实现)。

    Phase 2 切 Redis，接口不变。

    示例:
        memory = SessionMemory()
        await memory.add_message("s1", {"role": "user", "content": "你好"})
        ctx = await memory.get_context("s1")
        print(ctx)  # [{"role": "user", "content": "你好"}]
    """

    def __init__(self, max_messages: int = 20):
        """
        Args:
            max_messages: 每个会话保留的最大消息数 (轮数 × 2)
        """
        self.max_messages = max_messages
        self._sessions: dict[str, list[dict]] = {}   # 内存存储
        self._created_at: dict[str, float] = {}       # 会话创建时间
        self._last_active: dict[str, float] = {}      # 最后活跃时间

    def _ensure_session(self, session_id: str) -> None:
        """确保会话存在。"""
        if session_id not in self._sessions:
            self._sessions[session_id] = []
            self._created_at[session_id] = time.time()
        self._last_active[session_id] = time.time()

    async def add_message(self, session_id: str, message: dict) -> None:
        """添加一条消息到会话末尾。

        参数:
            session_id: 会话 ID
            message: {"role": "user", "content": "..."}
                     或 {"role": "assistant", "content": "...", "citations": [...], "model": "..."}

        示例:
            await memory.add_message("session_abc", {
                "role": "user",
                "content": "料筒温度异常怎么处理"
            })
        """
        self._ensure_session(session_id)

        # 长文本截断 (避免日志/代码粘贴撑爆上下文)
        msg = dict(message)
        content = msg.get("content", "")
        if len(content) > MAX_MESSAGE_LENGTH:
            msg["content"] = content[:MAX_MESSAGE_LENGTH] + f"\n... [已截断, 原文{len(content)}字符]"
            msg["truncated"] = True

        # 添加时间戳
        if "timestamp" not in msg:
            msg["timestamp"] = time.time()

        self._sessions[session_id].append(msg)

        # 保持窗口大小
        if len(self._sessions[session_id]) > self.max_messages:
            # 从头部弹旧消息
            self._sessions[session_id] = self._sessions[session_id][-self.max_messages:]

        logger.debug(f"会话 [{session_id}] 添加消息, 当前 {len(self._sessions[session_id])}/{self.max_messages} 条")

    async def get_context(
        self,
        session_id: str,
        max_messages: int | None = None,
        as_llm_format: bool = True,
    ) -> list[dict]:
        """获取当前会话的上下文消息。

        参数:
            session_id: 会话 ID
            max_messages: 返回的最大消息数 (默认使用实例配置)
            as_llm_format: True → 返回 {"role": "...", "content": "..."} 格式 (LLM 可直接用)
                          False → 返回完整消息 (含 citations, model 等扩展字段)

        返回: 消息列表，从旧到新排列

        示例:
            # 获取完整上下文，用于构建 LLM prompt
            llm_msgs = await memory.get_context("session_abc")
            llm_msgs.insert(0, {"role": "system", "content": system_prompt})

            # 获取原始消息 (含引用信息)
            raw = await memory.get_context("session_abc", as_llm_format=False)
            citations = raw[-1].get("citations", [])
        """
        self._ensure_session(session_id)
        limit = max_messages or self.max_messages
        messages = self._sessions[session_id][-limit:]

        if as_llm_format:
            return [{"role": m["role"], "content": m["content"]} for m in messages]

        return list(messages)

    async def get_last_user_message(self, session_id: str) -> Optional[dict]:
        """获取最后一条用户消息 (用于快捷指令获取用户输入)。

        示例:
            msg = await memory.get_last_user_message("session_abc")
            if msg:
                print(f"用户最后说: {msg['content']}")
        """
        self._ensure_session(session_id)
        msgs = self._sessions.get(session_id, [])
        for m in reversed(msgs):
            if m.get("role") == "user":
                return m
        return None

    async def get_message_count(self, session_id: str) -> int:
        """获取会话消息数。"""
        return len(self._sessions.get(session_id, []))

    async def delete_session(self, session_id: str) -> None:
        """删除整个会话 (用户主动清除 / 会话结束)。"""
        self._sessions.pop(session_id, None)
        self._created_at.pop(session_id, None)
        self._last_active.pop(session_id, None)
        logger.info(f"会话删除: {session_id}")

    async def clear_inactive(self, ttl_seconds: int = 7200) -> int:
        """清理过期会话 (2 小时无活跃)。

        返回: 清理的会话数

        应用于: 后台定时任务，避免内存泄漏。
        """
        now = time.time()
        expired = [
            sid for sid, last in self._last_active.items()
            if now - last > ttl_seconds
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._created_at.pop(sid, None)
            self._last_active.pop(sid, None)
        if expired:
            logger.info(f"清理过期会话: {len(expired)} 个")
        return len(expired)

    @property
    def session_count(self) -> int:
        return len(self._sessions)


# 全局单例
_session_memory: SessionMemory | None = None


def get_session_memory() -> SessionMemory:
    """获取会话记忆单例。"""
    global _session_memory
    if _session_memory is None:
        _session_memory = SessionMemory()
    return _session_memory
