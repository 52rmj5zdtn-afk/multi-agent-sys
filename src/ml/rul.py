"""
ml/rul.py — 预测性维护 RUL 推理服务客户端
=========================================
对应 sidecar 服务：predictive-maintenance-rul 的 src.agent_tool:app（端口 8002）
  POST /predict  JSON {device_id, readings, model} → {rul_cycles, padded, model_used, ...}
  GET  /health   就绪检查
"""
from src.core.config import get_settings
from src.ml.base import MLServiceClient


class RULClient(MLServiceClient):
    """设备剩余寿命（RUL）预测客户端。"""

    def __init__(self):
        settings = get_settings()
        super().__init__(
            base_url=settings.ml_rul_url,
            timeout=settings.ml_rul_timeout,
            service="rul",
        )

    async def predict(self, device_id: str, readings: list[dict], model: str = "lstm") -> dict:
        """对单台设备最近的传感器序列预测 RUL。

        Args:
            device_id: 设备 ID（落库 rul_predictions 用）
            readings:  逐循环传感器数据（原始量纲，时间升序），服务端取最后 window 行
            model:     lstm / transformer
        Returns:
            服务返回的 dict：rul_cycles / padded / window_size / model_used 等
        """
        return await self._post(
            "/predict",
            json={"device_id": device_id, "readings": readings, "model": model},
        )
