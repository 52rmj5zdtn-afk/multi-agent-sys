"""
ml/__init__.py — ML 推理服务客户端入口
======================================
按服务单例化客户端（与 get_session_memory / get_compiled_graph 同模式）。
节点不直接 new 客户端，统一从这里取，方便测试替换与 lifespan 关闭。

用法:
    from src.ml import get_vision_client, get_rul_client
    result = await get_vision_client().inspect(image_path)
"""
from functools import lru_cache

from src.ml.base import MLServiceClient  # noqa: F401  (re-export)
from src.ml.rul import RULClient
from src.ml.vision import VisionClient


@lru_cache
def get_vision_client() -> VisionClient:
    """视觉质检服务客户端单例。"""
    return VisionClient()


@lru_cache
def get_rul_client() -> RULClient:
    """RUL 预测服务客户端单例。"""
    return RULClient()


async def close_all() -> None:
    """应用关闭时释放 httpx 连接（lifespan 调用）。"""
    for getter in (get_vision_client, get_rul_client):
        client = getter()
        await client.close()
