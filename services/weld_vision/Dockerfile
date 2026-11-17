# 焊缝检测 sidecar 镜像（anomalib + torch CPU）
FROM python:3.12-slim

WORKDIR /srv
COPY services/__init__.py ./services/__init__.py
COPY services/weld_vision ./services/weld_vision

# torch CPU 轮（默认 PyPI 轮捆绑 CUDA，体积大 10 倍）
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        "torch>=2.2" "torchvision>=0.17" && \
    pip install --no-cache-dir \
        "fastapi>=0.110" "uvicorn[standard]" "pydantic>=2.6" \
        "anomalib>=2.0" "Pillow>=10.0" "numpy>=1.26"

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    HF_ENDPOINT=https://hf-mirror.com \
    WELD_VISION_MODEL_DIR=/models \
    WELD_VISION_HEATMAP_DIR=/app/data/uploads/heatmaps
EXPOSE 8001
CMD ["uvicorn", "services.weld_vision.app:app", "--host", "0.0.0.0", "--port", "8001"]
