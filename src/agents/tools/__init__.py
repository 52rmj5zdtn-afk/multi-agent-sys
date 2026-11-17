"""
agents/tools/__init__.py — MCP 工具注册表
==========================================
角色：定义系统中可被 Agent 调用的工具（MCP 风格：name + description +
      parameters schema + handler + 资源限制）。当前内置两个 ML 推理工具，
      由新增的 vision_inspection / predictive_maintenance 图节点消费。

工具与 sidecar 服务的关系：
  defect_inspection → ml-vision 服务 (industrial-anomaly-detection, :8001)
  predict_rul       → ml-rul 服务 (predictive-maintenance-rul, :8002)

使用方式:
    from src.agents.tools import get_ml_tools
    for tool in get_ml_tools():
        agent.register_tool(tool)      # BaseAgent.call_tool(name, **params) 调用

注意:
- handler 为 async 函数，BaseAgent.call_tool 会直接 await
- timeout_seconds 覆盖 ML 推理的最坏情况（排队 + CPU 推理），比客户端超时更宽
- 服务不可用时 handler 抛 MLServiceUnavailableError，由调用节点降级处理
"""

from src.agents.base import ToolDefinition
from src.ml import get_rul_client, get_vision_client


async def _vision_handler(image_path: str, model: str = "patchcore") -> dict:
    return await get_vision_client().inspect(image_path, model=model)


async def _rul_handler(device_id: str, readings: list[dict], model: str = "lstm") -> dict:
    return await get_rul_client().predict(device_id, readings, model=model)


VISION_INSPECTION_TOOL = ToolDefinition(
    name="defect_inspection",
    description=(
        "对产线相机拍摄的产品图片做无监督缺陷检测（Patchcore/EfficientAd）。"
        "输入图片路径，返回是否缺陷、异常分数(0~1)、判定阈值和像素级热力图路径。"
        "异常分数超过阈值应判 NG 并触发工单流转。"
    ),
    parameters={"image_path": "str", "model": "str=patchcore"},
    handler=_vision_handler,
    max_calls_per_minute=60,
    timeout_seconds=15,
)

RUL_PREDICTION_TOOL = ToolDefinition(
    name="predict_rul",
    description=(
        "根据设备最近若干个循环的传感器序列预测剩余使用寿命(RUL，单位循环)。"
        "输入设备 ID 与逐循环传感器 readings（原始量纲、时间升序），"
        "返回剩余循环数、窗口是否补齐、所用模型。RUL 低于预警阈值应触发预防性维修工单。"
    ),
    parameters={"device_id": "str", "readings": "list[dict]", "model": "str=lstm"},
    handler=_rul_handler,
    max_calls_per_minute=120,
    timeout_seconds=10,
)


def get_ml_tools() -> list[ToolDefinition]:
    """返回全部 ML 推理工具定义。"""
    return [VISION_INSPECTION_TOOL, RUL_PREDICTION_TOOL]
