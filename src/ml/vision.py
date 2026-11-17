"""
ml/vision.py — 视觉质检推理服务客户端
=====================================
对应 sidecar 服务：industrial-anomaly-detection 的 src.agent_tool:app（端口 8001）
  POST /inspect  multipart 上传图片 → {defect_detected, anomaly_score, threshold, anomaly_map_path, ...}
  GET  /health   就绪检查
"""
from pathlib import Path

from src.core.config import get_settings
from src.ml.base import MLServiceClient


class VisionClient(MLServiceClient):
    """视觉缺陷检测客户端。同步 IO（读文件）在调用方完成，这里只发 HTTP。"""

    def __init__(self):
        settings = get_settings()
        super().__init__(
            base_url=settings.ml_vision_url,
            timeout=settings.ml_vision_timeout,
            service="vision",
        )

    async def inspect(self, image_path: str, model: str = "patchcore",
                      threshold: float | None = None, tag: str | None = None) -> dict:
        """对单张图片做缺陷检测。

        Args:
            image_path: 图片文件路径（由节点从 state.attachments 解析）
            model:      patchcore / efficientad
            threshold:  可选产品级判定阈值覆盖（换型场景，透传给服务端重新判定）
            tag:        检测通道（2d 外观 / depth 3D 深度图），服务端按通道加载对应模型
        Returns:
            服务返回的 dict：defect_detected / anomaly_score / threshold /
            anomaly_map_path / inferred_at / measurements(3D) 等
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"待检图片不存在: {image_path}")
        content = path.read_bytes()
        params = {"model": model}
        if threshold is not None:
            params["threshold"] = threshold
        if tag is not None:
            params["tag"] = tag
        return await self._post(
            "/inspect",
            params=params,
            files={"file": (path.name, content, "image/png")},
        )
