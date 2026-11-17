# -*- coding: utf-8 -*-
"""端到端集成测试：真实 sidecar + 真实模型 + 图节点 + 临时数据库。

前置（模型产物存在则运行，否则跳过）:
  models/weld_rul/<4 台设备>/        python -m services.weld_rul.train
  models/weld_vision/patchcore-2d.pt  python -m services.weld_vision.train --tag 2d
  models/weld_vision/patchcore-depth.pt（3D 测量通道用桩验证几何，真实模型可选）

流程: 临时 SQLite → seed_weld_line → 拉起两个 sidecar（真实 uvicorn 子进程）
→ predictive_maintenance 节点消费 RUL sidecar 出预警工单
→ vision_inspection 节点消费视觉 sidecar 出质检记录与 NG 工单。
"""
import asyncio
import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MODELS = ROOT / "models"
RUL_READY = (MODELS / "weld_rul").exists() and any((MODELS / "weld_rul").glob("*/model.pt"))
VISION_READY = (MODELS / "weld_vision" / "weights" / "torch" / "padim-2d.pt").exists() \
    or (MODELS / "weld_vision" / "padim-2d.pt").exists()

pytestmark = pytest.mark.skipif(not (RUL_READY and VISION_READY),
                                reason="需要真实训练产物（models/weld_rul + models/weld_vision）")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_health(url: str, proc: subprocess.Popen, timeout: float = 120.0) -> dict:
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"sidecar 进程提前退出 rc={proc.returncode}")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as resp:
                import json
                return json.loads(resp.read())
        except Exception:
            time.sleep(1.0)
    raise TimeoutError(f"sidecar 健康检查超时: {url}")


@pytest.fixture(scope="module")
def sidecars():
    env = dict(os.environ, PYTHONUTF8="1",
               WELD_RUL_MODEL_DIR=str(MODELS / "weld_rul"),
               WELD_VISION_MODEL_DIR=str(MODELS / "weld_vision"),
               WELD_VISION_HEATMAP_DIR=str(ROOT / "data" / "uploads" / "heatmaps"))
    rul_port, vis_port = _free_port(), _free_port()
    py = sys.executable
    rul = subprocess.Popen([py, "-m", "uvicorn", "services.weld_rul.app:app",
                            "--port", str(rul_port), "--log-level", "warning"],
                           cwd=str(ROOT), env=env)
    vis = subprocess.Popen([py, "-m", "uvicorn", "services.weld_vision.app:app",
                            "--port", str(vis_port), "--log-level", "warning"],
                           cwd=str(ROOT), env=env)
    try:
        _wait_health(f"http://127.0.0.1:{rul_port}", rul)
        _wait_health(f"http://127.0.0.1:{vis_port}", vis)
        yield {"rul": f"http://127.0.0.1:{rul_port}", "vision": f"http://127.0.0.1:{vis_port}"}
    finally:
        for p in (rul, vis):
            p.terminate()
        for p in (rul, vis):
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()


@pytest.fixture()
def weld_env(tmp_path, monkeypatch, sidecars):
    """临时数据库 + 种子数据 + sidecar 地址注入配置。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/e2e.db")
    monkeypatch.setenv("ML_RUL_URL", sidecars["rul"])
    monkeypatch.setenv("ML_VISION_URL", sidecars["vision"])

    # 不能 reload(database)：模块重载会产生新的 Base，原 Base 上已映射的表
    # 不会在新 metadata 上建表。只重置引擎缓存，让 get_engine 按新 URL 重建。
    import src.core.database as database
    database._engine = None
    database._session_factory = None
    # get_settings 带 lru_cache：若会话中已被提前填充，清掉以读到本夹具的环境变量
    from src.core.config import get_settings
    get_settings.cache_clear()
    from src.core.database import init_db
    asyncio.run(init_db())

    sys.path.insert(0, str(ROOT / "scripts"))
    from seed_weld_line import seed as _seed
    asyncio.run(_seed())

    # 客户端读环境变量里的 URL
    from src.ml.rul import RULClient
    from src.ml.vision import VisionClient
    return {"rul": RULClient(), "vision": VisionClient()}


def test_pdm_node_creates_work_order(weld_env):
    from src.graph.nodes.predictive_maintenance import create_pdm_node
    from src.graph.state import AgentState

    state = AgentState(user_query="激光焊接主机健康度怎么样", user_context=None)
    node = create_pdm_node()
    out = asyncio.run(node(state))
    rul = out["ml_results"]["rul"]
    assert "rul_cycles" in rul and rul["rul_cycles"] >= 0
    assert rul["health_score"] is not None

    from src.core.database import get_session_factory
    from src.data.models import WorkOrder
    from sqlalchemy import select
    async def check():
        async with get_session_factory()() as db:
            rows = (await db.execute(select(WorkOrder).where(
                WorkOrder.equipment_id == "line03-weld-laser-01"))).scalars().all()
            return rows
    orders = asyncio.run(check())
    assert orders, "种子遥测末段已深度劣化，应触发预测性维修工单"
    assert orders[0].fault_code == "PREDICT_RUL"


def test_vision_node_inspects_good_and_ng(weld_env, tmp_path):
    from src.graph.nodes.vision_inspection import create_vision_node
    from src.graph.state import AgentState

    from services.weld_vision.synth_weld import render_depth_image, render_weld_image
    good = tmp_path / "good.png"
    ng = tmp_path / "ng.png"
    render_weld_image(random.Random(30), False)[0].save(good)
    # 氧化变色: 合成数据上 2D 通道稳定检出（气孔/裂纹级微小缺陷需真实产线图微调）
    render_weld_image(random.Random(31), True, "oxidation")[0].save(ng)
    render_depth_image(render_weld_image(random.Random(32), True, "lack_of_fusion")[1]).save(
        tmp_path / "depth.png")

    node = create_vision_node()
    results = {}
    for name, path in [("good", good), ("ng", ng)]:
        state = AgentState(user_query="焊缝检测", user_context=None,
                           attachments=[{"type": "image", "path": str(path), "url": "",
                                         "product": "weld-seam-2d"}],
                           ml_results={}, agent_outputs={}, errors=[])
        out = asyncio.run(node(state))
        results[name] = out["ml_results"]["vision"]

    assert results["good"].get("defect_detected") is False, results
    assert results["ng"].get("defect_detected") is True, results

    from src.core.database import get_session_factory
    from src.data.models import InspectionRecord
    from sqlalchemy import select
    async def count():
        async with get_session_factory()() as db:
            return len((await db.execute(select(InspectionRecord))).scalars().all())
    assert asyncio.run(count()) == 2


def test_vision_depth_channel_via_product_routing(weld_env, tmp_path):
    """3D 通道: 节点按产品编码（-3d 结尾）路由 depth 通道，结果含几何测量与超窗拦截。"""
    from src.graph.nodes.vision_inspection import create_vision_node
    from src.graph.state import AgentState
    from services.weld_vision.synth_weld import render_depth_image, render_weld_image
    depth = tmp_path / "depth.png"
    render_depth_image(render_weld_image(random.Random(40), True, "lack_of_fusion")[1]).save(depth)
    state = AgentState(user_query="焊缝3D检测", user_context=None,
                       attachments=[{"type": "image", "path": str(depth), "url": "",
                                     "product": "weld-seam-3d"}],
                       ml_results={}, agent_outputs={}, errors=[])
    out = asyncio.run(create_vision_node()(state))
    vision = out["ml_results"]["vision"]
    assert vision["tag"] == "depth"
    assert vision["measurements"] and "bead_width_mm" in vision["measurements"]
    assert vision["defect_detected"] is True       # 未熔合: 几何超窗 + 异常分数双判据
    assert vision["geometry_violations"]
