# RUL（虚焊预警）sidecar 镜像（纯 torch CPU，无 anomalib）
FROM python:3.12-slim

WORKDIR /srv
COPY services/__init__.py ./services/__init__.py
COPY services/weld_rul ./services/weld_rul
COPY scripts/seed_weld_line.py ./scripts/seed_weld_line.py

# torch CPU 轮；scripts/seed_weld_line.py 是信号定义的单一来源
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        "torch>=2.2" && \
    pip install --no-cache-dir "numpy>=1.26" "fastapi>=0.110" "uvicorn[standard]" "pydantic>=2.6"

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    WELD_RUL_MODEL_DIR=/models
EXPOSE 8002
CMD ["uvicorn", "services.weld_rul.app:app", "--host", "0.0.0.0", "--port", "8002"]
