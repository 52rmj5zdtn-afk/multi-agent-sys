"""services/weld_rul — 焊接工位 RUL（虚焊预警）推理服务包。

数据流: simulation(run-to-failure 仿真) → dataset(滑窗数据集) → model(LSTM 回归)
→ train(训练入口) → app(FastAPI :8002，契约同 src/ml/rul.py 客户端)。
"""
