"""app.py — 焊缝检测 sidecar（:8001，契约对齐 src/ml/vision.py）
=============================================================
端点:
  GET  /health → {status, models}
  POST /inspect  multipart 上传图片，params: model(patchcore|efficientad),
       tag(2d|depth，由产品映射决定), threshold(可选覆盖)
       → {defect_detected, anomaly_score, threshold, anomaly_map_path, image,
          model, inferred_at, measurements?, geometry_violations}

热力图保存到 HEATMAP_DIR（默认 <repo>/data/uploads/heatmaps），
路径写入 inspection_records.heatmap_path 供人工复核追溯。

启动:  uvicorn services.weld_vision.app:app --port 8001
环境:  WELD_VISION_MODEL_DIR（默认 <repo>/models/weld_vision）
"""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.datastructures import QueryParams
from fastapi import Request
from PIL import Image

from services.weld_vision.infer import WeldInspector

_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "weld_vision"
MODEL_DIR = Path(os.environ.get("WELD_VISION_MODEL_DIR", str(_DEFAULT_MODEL_DIR)))
HEATMAP_DIR = Path(os.environ.get("WELD_VISION_HEATMAP_DIR",
                                  str(Path(__file__).resolve().parents[2] / "data" / "uploads" / "heatmaps")))

app = FastAPI(title="weld-vision-sidecar", version="1.0.0")
_inspector: WeldInspector | None = None


@app.on_event("startup")
def startup() -> None:
    global _inspector
    _inspector = WeldInspector(MODEL_DIR)
    HEATMAP_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/health")
def health() -> dict:
    if MODEL_DIR.exists():
        models = sorted(p.name for p in (MODEL_DIR / "weights" / "torch").glob("*.pt"))
        if not models:
            models = sorted(p.name for p in MODEL_DIR.glob("*.pt"))
    else:
        models = []
    return {"status": "ok", "model_dir": str(MODEL_DIR), "models": models,
            "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.post("/inspect")
async def inspect(request: Request, file: UploadFile = File(...)) -> dict:
    params: QueryParams = request.query_params
    model_name = params.get("model", "patchcore")
    tag = params.get("tag", "2d")
    threshold = params.get("threshold")
    if _inspector is None:
        raise HTTPException(503, "服务未初始化")

    try:
        image = Image.open(file.file).convert("RGB")
    except Exception as e:
        raise HTTPException(422, f"无法解析图片: {e}")

    heatmap_path = HEATMAP_DIR / f"{time.strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}.png"
    try:
        result = _inspector.inspect(
            image, model_name=model_name, tag=tag,
            threshold=float(threshold) if threshold is not None else None,
            heatmap_path=heatmap_path,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"推理失败: {e}")

    result["image"] = file.filename or "uploaded.png"
    return result
