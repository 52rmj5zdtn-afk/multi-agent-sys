"""
app.py — FastAPI 应用入口
=========================
制造业多 Agent 生产运维数字助手的主 API 服务。

启动方式:
  uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

应用生命周期:
  1. startup: 初始化日志、预热模型、启动后台任务
  2. running: 处理请求
  3. shutdown: 清理资源

模块加载顺序:
  1. setup_logging() — 日志系统最先初始化
  2. get_compiled_graph() — 预热 LangGraph (首次调用会加载 Embedding 模型)
  3. 启动 FastAPI — 开始监听请求

架构分层:
  请求 → Middleware (注入检测/鉴权/限流) → Route → Graph → Agent → LLM
                ↑                                    ↓
            [异常处理] ← ← ← ← ← ← ← ← ← ← ← ← [异常传播]

注意事项:
- 生产环境不要用 --reload (热重载有内存泄漏)
- 端口 8000 是开发默认值，生产通过环境变量 PORT 指定
- 前端静态文件通过 /app 路径 serve (Phase 2)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.core.config import get_settings
from src.core.logging import setup_logging, logger
from src.core.exceptions import ManufacturingAgentError
from src.api.routes import conversation, health
from src.api.deps import get_compiled_graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。

    Startup:
    1. 初始化日志
    2. 预热 LangGraph (加载 embedding 模型等)
    3. 日志启动信息

    Shutdown:
    1. 清理会话内存
    2. 日志关闭信息
    """
    # Startup
    setup_logging()
    settings = get_settings()
    logger.info(f"制造业多Agent生产运维助手 v0.1.0 启动中...")
    logger.info(f"LLM: 主={settings.default_model}, 备={settings.fallback_model}")
    logger.info(f"嵌入模型: {settings.embedding_model} on {settings.embedding_device}")

    # 预热: 确保 Graph 在第一个请求到达前编译完成
    try:
        from src.api.deps import get_compiled_graph as _get_graph
        _get_graph()
        logger.info("LangGraph 工作流已就绪")
    except Exception as e:
        logger.error(f"Graph 预热失败: {e}")

    logger.info("应用启动完成 (种子数据通过 POST /api/v1/admin/seed 加载)")

    yield

    # Shutdown
    logger.info("应用关闭中...")
    from src.memory.session import get_session_memory
    memory = get_session_memory()
    expired = await memory.clear_inactive(ttl_seconds=0)  # 清理全部
    logger.info(f"清理了 {expired} 个会话")
    logger.info("应用已关闭")


app = FastAPI(
    title="制造业多Agent生产运维助手",
    description="基于多Agent架构的离散制造生产运维数字助手",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS (开发环境允许所有来源)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
app.include_router(conversation.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
from src.api.routes import admin
app.include_router(admin.router, prefix="/api/v1")

# 全局异常处理
@app.exception_handler(ManufacturingAgentError)
async def manufacturing_error_handler(request: Request, exc: ManufacturingAgentError):
    """将自定义异常转换为统一错误响应。"""
    return JSONResponse(
        status_code=_http_status(exc.code),
        content={
            "code": int(exc.code[1:]),  # "E10001" → 10001
            "message": exc.message,
            "detail": exc.detail,
        },
    )


@app.exception_handler(Exception)
async def general_error_handler(request: Request, exc: Exception):
    logger.error(f"未捕获异常: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "code": 50000,
            "message": "系统内部错误",
            "detail": {"error": str(exc)},
        },
    )


def _http_status(error_code: str) -> int:
    """根据错误码返回对应的 HTTP 状态码。"""
    if error_code.startswith("E1"):
        return 403  # 安全相关 → 403
    elif error_code.startswith("E2"):
        return 503  # 模型不可用 → 503
    elif error_code.startswith("E3"):
        return 503  # 检索不可用 → 503
    elif error_code.startswith("E4"):
        return 500  # Agent 异常 → 500
    elif error_code.startswith("E5"):
        return 502  # 集成异常 → 502
    return 500


@app.get("/")
async def root():
    """根路径 — 返回服务信息。"""
    return {
        "name": "制造业多Agent生产运维助手",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/api/v1/health",
    }
