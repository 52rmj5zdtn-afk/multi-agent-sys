# -*- coding: utf-8 -*-
"""最小冒烟测试（python tests/test_smoke.py；pytest 亦可收集）。

验证：图状态新字段、ML 异常体系、工具注册表完整性、专用模块可导入。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_state_has_ml_fields():
    from src.graph.state import AgentState
    s = AgentState()
    assert hasattr(s, "attachments") and s.attachments == []
    assert hasattr(s, "ml_results") and s.ml_results == {}


def test_ml_exception_hierarchy():
    from src.core.exceptions import MLServiceUnavailableError, IntegrationError
    e = MLServiceUnavailableError("x")
    assert e.code == "E50003" and isinstance(e, IntegrationError)


def test_tools_registry():
    from src.agents.tools import get_ml_tools
    tools = {t.name: t for t in get_ml_tools()}
    assert set(tools) == {"defect_inspection", "predict_rul"}
    assert tools["defect_inspection"].handler and tools["predict_rul"].handler


def test_type_profiles_importable():
    import importlib
    for mod in ["src.graph.nodes.vision_inspection", "src.graph.nodes.predictive_maintenance",
                "src.api.routes.devices", "src.api.routes.inspections", "src.api.routes.work_orders",
                "src.ml"]:
        importlib.import_module(mod)


def test_work_order_state_machine_docs():
    import inspect
    from src.api.routes import work_orders
    src = inspect.getsource(work_orders)
    assert "assign" in src and "resolve" in src and "409" in src


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("\nALL SMOKE TESTS PASSED")
