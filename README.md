# 新能源锂电模组线多 Agent 生产运维数字助手

基于 LangGraph 多 Agent 架构的锂电池模组产线运维 AI 助手，覆盖电芯上料 / 模组堆叠 / 激光焊接（侧缝焊 & Busbar 焊）/ 涂胶 / EOL 测试等新能源模组线核心工序（产线实例：巢湖三拉环线，西门子 S7-1500 SICAR 标准程序）。

## 业务闭环

```
设备报警 → 故障诊断 Agent（根因分析）→ 知识库 Agent（维修 SOP 匹配）
→ 排程 Agent（产能影响评估）→ 工单推送 → 维修执行（接单/维修/完结回写）
→ 质检 Agent（视觉检测 + 人工复核，误报率反哺阈值）→ 报表 Agent（OEE/良率更新）
→ 知识沉淀（写入经验库）

预测性维护支线：传感器遥测 → RUL 预测（结合设备台账翻译为健康分/剩余天数）
→ 低于设备级阈值自动建预防性维修工单（关键部件级）
```

## 技术栈

| 层级 | 技术 |
|------|------|
| Agent 框架 | LangGraph（图状态机 + Checkpoint） |
| API | FastAPI + WebSocket（SSE 流式） |
| 向量检索 | Milvus Lite + BM25（rank-bm25）混合检索 + BGE-Reranker |
| 嵌入模型 | BAAI/bge-large-zh-v1.5 / bge-small-zh-v1.5 |
| 数据库 | SQLite（开发）/ PostgreSQL（生产） |
| 缓存 / 会话 | Redis |
| LLM 主模型 | DeepSeek-chat（可配置） |
| LLM 降级 | 多模型路由 + 熔断器 + 规则兜底 |
| 视觉质检 | anomalib Patchcore/EfficientAd（sidecar :8001） |
| 预测性维护 | LSTM RUL（sidecar :8002；虚焊预警领域模型 + C-MAPSS 演示模型） |
| OCR | EasyOCR（中英文） |
| 前端 | Vue3 + Element Plus + Vite |
| 可观测 | OpenTelemetry + Prometheus + Grafana |
| 部署 | Docker Compose / systemd + nginx |

## 目录结构

```
manufacturing-agent/
├── src/
│   ├── api/                    # FastAPI 接入层
│   │   ├── app.py              # 应用入口 & 生命周期
│   │   ├── deps.py             # 依赖注入
│   │   ├── routes/             # 路由
│   │   │   ├── conversation.py # 对话（SSE 流式 + 图片聊天）
│   │   │   ├── knowledge.py    # 知识库管理（上传/搜索/删除）
│   │   │   ├── admin.py        # 管理（种子数据/种子用户）
│   │   │   ├── auth.py         # JWT 认证
│   │   │   └── health.py       # 健康检查
│   │   ├── middleware/         # 中间件
│   │   │   ├── auth.py         # JWT 鉴权
│   │   │   └── ratelimit.py    # API 限流
│   │   └── schemas/            # Pydantic 请求/响应模型
│   ├── graph/                  # LangGraph 编排
│   │   ├── state.py            # AgentState 定义（含 attachments / ml_results）
│   │   ├── builder.py          # 图构建
│   │   ├── checkpoint.py       # 检查点持久化
│   │   ├── nodes/              # 工作流节点
│   │   │   ├── guard.py        # 安全守护（注入检测 + RBAC）
│   │   │   ├── router.py       # 意图路由（含 ML 节点路由与执行排序）
│   │   │   ├── knowledge.py    # 知识检索
│   │   │   ├── vision_inspection.py      # 视觉质检（ML 工具型，检测结果供 quality 消费）
│   │   │   ├── predictive_maintenance.py # 预测性维护（ML 工具型，RUL 供 inspection 消费）
│   │   │   ├── diagnosis.py    # 故障诊断
│   │   │   ├── inspection.py   # 巡检预警（升级：注入 RUL 做预警分级）
│   │   │   ├── scheduling.py   # 排程优化
│   │   │   ├── quality.py      # 质量分析（升级：基于视觉检测结果做处置决策）
│   │   │   ├── report.py       # 数据报表
│   │   │   ├── aggregate.py    # 多 Agent 结果聚合
│   │   │   └── memory_node.py  # 记忆持久化
│   │   └── edges.py            # 条件边/路由逻辑
│   ├── agents/                 # Agent 定义
│   │   ├── base.py             # BaseAgent + ToolDefinition（MCP 风格）
│   │   ├── tools/              # ML 工具注册（defect_inspection / predict_rul）
│   │   └── prompts/            # System Prompt 模板（Jinja2）
│   ├── retrieval/              # 混合检索
│   │   ├── embedding.py        # 嵌入服务（BGE 模型）
│   │   ├── bm25.py             # BM25 关键词检索
│   │   ├── dense.py            # 向量检索（Milvus Lite / 内存）
│   │   ├── reranker.py         # Cross-encoder 重排序
│   │   ├── hybrid.py           # 混合检索融合（RRF）
│   │   ├── chunker.py          # 语义分块（表级/SOP步骤/标题）
│   │   ├── ingestion.py        # 文档摄入管线（PDF/Word/Excel）
│   │   └── sync.py             # 双写一致性
│   ├── memory/                 # 记忆管理
│   │   ├── session.py          # 短期记忆（Redis / 内存）
│   │   ├── persistent.py       # 长期记忆（Milvus）
│   │   └── compressor.py       # LLM 上下文压缩
│   ├── model/                  # 模型层
│   │   ├── router.py           # 多模型路由（复杂度分类）
│   │   ├── fallback.py         # 降级策略（主→备→规则兜底）
│   │   ├── cache.py            # 语义缓存
│   │   ├── cost.py             # Token 成本追踪
│   │   ├── concurrency.py      # LLM 调用排队
│   │   └── rule_engine.py      # 规则引擎兜底
│   ├── security/               # 安全
│   │   ├── injection.py        # Prompt Injection 检测
│   │   ├── sanitize.py         # 输出脱敏
│   │   ├── rbac.py             # 基于角色的权限控制
│   │   └── audit.py            # 审计日志
│   ├── observability/          # 可观测
│   │   ├── tracing.py          # OpenTelemetry 追踪
│   │   ├── langfuse.py         # LLM 专属观测
│   │   └── metrics.py          # Prometheus 指标
│   ├── integration/            # 外部集成
│   │   └── ocr.py              # EasyOCR 图片文字提取
│   ├── ml/                     # ML 推理服务客户端（sidecar HTTP 调用）
│   │   ├── base.py             # 客户端基类（超时/重试/熔断/指标）
│   │   ├── vision.py           # 视觉质检客户端 (:8001)
│   │   └── rul.py              # RUL 预测客户端 (:8002)
│   └── core/                   # 基础设施
│       ├── config.py           # 全局配置（Pydantic Settings）
│       ├── database.py         # 数据库引擎（SQLAlchemy async）
│       ├── redis_client.py     # Redis 客户端
│       ├── retry.py            # 重试 + 熔断器（指数退避）
│       ├── events.py           # 事件总线
│       ├── exceptions.py       # 自定义异常
│       └── logging.py          # 日志配置
├── frontend/
│   ├── chat/                   # 智能助手前端（Vue3）
│   └── admin/                  # 知识库管理后台（Vue3）
├── data/
│   ├── seeds/                  # 种子数据（设备手册/SOP/故障案例）
│   ├── documents/              # 文档存储
│   ├── uploads/                # 上传文件
│   └── finetune/               # 微调数据集
├── evaluation/                 # 评测体系
│   ├── runner.py               # 评测执行器
│   ├── report.json             # 评测报告
│   └── benchmark/              # 测试集（故障诊断/SOP问答/注入测试）
├── deployment/
│   ├── docker-compose.yml      # 一键部署
│   ├── Dockerfile              # 后端镜像
│   ├── Dockerfile.frontend     # 前端镜像
│   ├── nginx.conf              # Nginx 配置
│   └── grafana_dashboard.json  # Grafana 监控面板
├── scripts/
│   ├── start.sh / start.bat    # 开发启动
│   ├── deploy.sh               # 服务器部署
│   ├── seed_demo_device.py     # C-MAPSS 演示数据（激光器退化演示，类比涡扇发动机 RUL 标准数据集）
│   ├── seed_weld_line.py       # 锂电模组线焊接工位演示数据（虚焊预警+焊缝检测）
│   └── finetune/               # LoRA 微调脚本
├── services/                   # 仓库内置 ML sidecar 与边缘采集（契约对齐 src/ml 客户端）
│   ├── weld_rul/               # 虚焊预警 RUL 服务 :8002（仿真/LSTM/训练/FastAPI）
│   ├── weld_vision/            # 焊缝检测服务 :8001（合成数据/anomalib Padim/3D 几何测量）
│   ├── edge_collector/         # 边缘采集器：S7-1500 OPC UA → MQTT（SQLite 断线补传）
│   └── telemetry_consumer/     # 遥测消费者：MQTT → FastAPI 遥测路由/直写库
├── deployment/
│   ├── docker-compose.yml      # 一键部署（含 mqtt-broker / edge-collector / telemetry-consumer）
│   └── mosquitto.conf          # 产线内网 broker（M1 最小配置，M2 加固清单见文件内注释）
└── tests/                      # 单元测试 & 集成测试
```

## 快速开始

### 环境要求

- Python >= 3.10
- Node.js >= 18
- Redis（可选，不可用时自动降级为内存模式）

### 1. 安装依赖

```bash
# 后端
pip install -e .

# 前端
cd frontend/chat && npm install
cd frontend/admin && npm install
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少填写：
#   DEEPSEEK_API_KEY=sk-xxxxx
#   DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

### 3. 启动开发服务

```bash
# 后端（端口 8000）
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

# 智能助手前端（端口 5173）
cd frontend/chat && npm run dev

# 管理后台前端（端口 5174）
cd frontend/admin && npm run dev
```

### 4. Docker Compose 部署

```bash
cd deployment
docker-compose up -d
```

## 生产部署

服务器部署脚本：`scripts/deploy.sh`

```bash
# 在服务器上执行
bash scripts/deploy.sh
```

部署后访问：
- 智能助手：`http://<服务器IP>`
- 管理后台：`http://<服务器IP>/admin`
- API 文档：`http://<服务器IP>/docs`
- 健康检查：`http://<服务器IP>/api/v1/health`
- Prometheus 指标：`http://<服务器IP>/metrics`

## 默认用户

演示环境预置 9 个用户，密码通过环境变量 `DEMO_PASSWORD` 配置。

| 用户名 | 角色 | 车间 |
|--------|------|------|
| worker_zhang | 产线工人 | A |
| maintainer_li | 维修工 | A |
| shift_lead_wang | 班组长 | A |
| director_zhao | 车间主任 | A |
| engineer_chen | 工艺工程师 | A |
| manager_zhou | 厂长 | 全部 |
| worker_sun | 产线工人 | B |
| maintainer_huang | 维修工 | B |
| shift_lead_liu | 班组长 | B |

## 功能特性

### 多 Agent 协作

工作流：Guard（安全检测）→ Router（意图分类）→ Specialist Agents（并行）→ Aggregate（结果聚合）→ Memory（持久化）

| Agent | 功能 |
|-------|------|
| Vision Inspection | 视觉质检：调 Patchcore/EfficientAd 检测产品图片，NG 自动落工单；结果供 Quality 做处置决策 |
| Predictive Maintenance | 预测性维护：基于传感器时序预测设备 RUL，低于阈值自动建预防性维修工单；结果供 Inspection 做预警分级 |
| Knowledge | 混合检索（BM25 + 向量 + Re-ranker），从知识库召回相关文档 |
| Diagnosis | 故障根因分析，结合检索结果和设备报警码推理 |
| Inspection | 巡检预警，结合 RUL 预测分析设备状态给出建议 |
| Scheduling | 排程优化，评估设备故障对产能的影响 |
| Quality | 质量分析，基于视觉检测结果决策停机/降级/放行 |
| Report | 数据报表，生成 OEE/良率等统计报告 |

### 混合检索

```
Query → 查询分类 → [BM25 + Dense(Milvus)] → RRF 融合 → BGE-Reranker → Top-K
```

- **精确匹配**（型号/报警码）：BM25 权重 0.7
- **混合查询**（型号 + 描述）：BM25:Dense = 0.5:0.5
- **语义查询**（故障描述）：Dense 权重 0.7
- 支持 PDF / Word / Excel / TXT / MD / CSV 上传

### 记忆系统

| 类型 | 存储 | 用途 |
|------|------|------|
| 短期记忆 | Redis List（滑动窗口） | 当前会话上下文 |
| 长期记忆 | Milvus（向量） | 跨会话故障经验召回 |
| 上下文压缩 | LLM 摘要 | Token 超阈值自动触发 |
| 知识沉淀 | 自动评估 → 写入经验库 | 高质量对话自动归档 |

### 安全防护

- **Prompt Injection 检测**：正则 + 关键词 + 模式匹配
- **RBAC**：6 种角色（工人/维修工/班组长/车间主任/工艺工程师/厂长），车间级隔离
- **JWT 认证**：bcrypt 密码哈希，可配置过期时间
- **输出脱敏**：成本数据/人员信息自动脱敏
- **审计日志**：全操作记录
- **API 限流**：基于 IP + 用户的双重限流

### 模型路由 & 降级

```
简单任务 → Qwen-turbo（便宜）
复杂推理 → DeepSeek-chat（强推理）
报表生成 → 规则引擎（不走 LLM）

降级链：主模型 → 备选模型 → 规则引擎兜底
熔断器：连续失败 5 次 → 熔断 30s → 半开探测 → 恢复/重熔
```

### 可观测

- **OpenTelemetry**：全链路追踪（请求 → 路由 → 检索 → LLM → 响应）
- **Prometheus**：请求 QPS、延迟、Token 消耗、检索延迟
- **Grafana**：预配置监控面板
- **Langfuse**：LLM 专属观测（可选）

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/auth/login` | 用户登录 |
| GET | `/api/v1/auth/me` | 当前用户信息 |
| POST | `/api/v1/conversation/chat` | SSE 流式对话 |
| POST | `/api/v1/conversation/image-chat` | 图片对话（入图：视觉质检 + 多 Agent 图） |
| POST | `/api/v1/devices/{id}/telemetry` | 批量写入设备遥测（班组长+，幂等去重，卡死传感器校验） |
| GET | `/api/v1/devices` | 设备列表 + 最新 RUL 预测 |
| GET | `/api/v1/devices/{id}/telemetry` | 查询最近遥测 |
| GET | `/api/v1/inspections` | 质检记录列表（复核状态过滤） |
| POST | `/api/v1/inspections/{id}/review` | 人工复核（confirmed_defect / false_positive，班组长+） |
| GET | `/api/v1/inspections/stats` | 误报率统计（按产品，反哺阈值校准） |
| GET | `/api/v1/work-orders` | 工单列表（维修派单台） |
| PUT | `/api/v1/work-orders/{id}` | 工单状态推进（接单/维修/完结，维修工+） |
| GET | `/api/v1/conversation/sessions` | 会话列表 |
| GET | `/api/v1/conversation/session/{id}/restore` | 恢复会话 |
| DELETE | `/api/v1/conversation/{id}` | 删除会话 |
| POST | `/api/v1/knowledge/upload` | 上传知识文档 |
| GET | `/api/v1/knowledge/documents` | 文档列表 |
| POST | `/api/v1/knowledge/search` | 知识检索测试 |
| DELETE | `/api/v1/knowledge/documents/{title}` | 删除文档 |
| POST | `/api/v1/admin/seed` | 加载种子数据 |
| GET | `/api/v1/health` | 健康检查 |
| GET | `/metrics` | Prometheus 指标 |

## 评测体系

```
evaluation/benchmark/
├── fault_cases.json      # 故障诊断评测
├── sop_qa.json           # SOP 问答评测
├── alarm_cases.json      # 报警场景评测
├── safety_eval.json      # 安全防护评测
├── injection_test.json   # 注入检测评测
└── rbac_test.json        # 权限控制评测
```

运行评测：
```bash
python evaluation/runner.py
```

## LoRA 微调

```bash
# 数据清洗
python scripts/finetune/clean_data.py

# LoRA 训练
python scripts/finetune/train_lora.py --base_model qwen-2.5-14b-int4 --epochs 3
```

## 待实现（Roadmap）

- [x] 视觉质检（无监督异常检测 sidecar，已完成融合）✅
- [x] 预测性维护（RUL 预测 sidecar，已完成融合）✅
- [ ] MES/ERP 系统对接
- [x] OPC UA / Modbus 工业协议接入（OPC UA 边缘采集 M1 已完成，见上节）✅
- [x] 设备实时数据采集（MQTT → 遥测管线 M1 已完成）✅
- [ ] M2：订阅模式采集 + broker 鉴权/TLS + TimescaleDB 全量时序
- [ ] 预测性维护定时批量预警（APScheduler 周期任务）
- [ ] 实时告警推送（WebSocket）
- [ ] K8s 部署配置
- [ ] 工单系统联动
- [ ] 移动端 PWA 支持

## ML 融合说明（预测 + 视觉）

训练代码留在原子项目迭代，推理服务以 sidecar 接入：

| 能力 | 训练项目 | 推理服务 | 图内节点 |
|------|---------|---------|---------|
| 视觉质检（焊缝检测） | 仓库内置 services/weld_vision（anomalib Padim，2D 外观 + 3D 深度图双通道，含几何测量） | `uvicorn services.weld_vision.app:app --port 8001` | vision_inspection → quality |
| 视觉质检（通用外观） | industrial-anomaly-detection（anomalib Patchcore/EfficientAd，可选） | 同上 :8001 | vision_inspection → quality |
| 预测性维护（虚焊预警） | 仓库内置 services/weld_rul（run-to-failure 仿真 → LSTM，4 台焊接设备各一模型） | `uvicorn services.weld_rul.app:app --port 8002` | predictive_maintenance → inspection/scheduling |

```bash
# 1. 同步模型产物（checkpoints + scaler）到 models/
python scripts/sync_ml_models.py

# 2. 灌入演示设备遥测（C-MAPSS FD001 → demo-compressor-001）
python scripts/seed_demo_device.py

# 2b. （可选）锂电模组线演示：巢湖Line03 激光焊接工位
#     PdM 侧=虚焊预警（激光器/振镜/冷却水机/KUKA 机器人 4 台台账 + 劣化遥测）
#     视觉侧=焊缝检测（weld-seam-2d / weld-seam-3d @ OPMODE07-EM010）
python scripts/seed_weld_line.py

# 2c. 训练焊接工位 ML 模型（torch，conda dl 环境）
#     RUL（虚焊预警）: run-to-failure 仿真 → 每台设备一个 LSTM checkpoint
python -m services.weld_rul.train --units 240 --epochs 30
#     焊缝检测: 合成焊缝图（2D 外观 + 3D 深度图双通道）→ anomalib Padim
#     （CPU 上 PatchCore 的 k-center 核心集采样代价过高，Padim 单次前向统计量训练 ~2 分钟）
#     Windows 注意: PYTHONUTF8=1 避免中文路径/控制台编码问题
#     （train.py 已内置 anomalib ASCII 路径守卫修复与 HF_ENDPOINT 镜像默认值）
python -m services.weld_vision.train --model padim --tag 2d --image-size 256
python -m services.weld_vision.train --model padim --tag depth --image-size 256
#     遥测特征名与 TIA 程序 IF_HMI.Laser / IF_HMI.Galvo / IO_HMI 信号对应，
#     真实产线由 OPC UA 网关按同名特征写入即可替换演示数据；
#     模型产物在 models/weld_rul/<device>/ 与 models/weld_vision/*.pt

# 3. ML sidecar 启动（或 docker-compose 一并拉起）
#    uvicorn services.weld_vision.app:app --port 8001
#    uvicorn services.weld_rul.app:app --port 8002

# 3. 两个训练项目各自构建镜像后一键启动
#    cd <industrial-anomaly-detection> && docker build -t ml-vision .
#    cd <predictive-maintenance-rul>  && docker build -t ml-rul .
cd deployment && docker-compose up -d
```

- `ML_ENABLED=false` 时系统回退为纯 LLM 行为（融合前模式），可无损回滚
- ML 服务不可达时节点自动降级（写 state.errors），LLM 节点照常工作
- 视觉判定阈值按产品配置（`inspection_products` 表，多品类共线换型），误报率在 `/inspections/stats` 复盘后回调
- RUL 输出经设备台账（`devices` 表）翻译为**健康分/预计剩余天数/关键部件**，工单对象是部件而不是"修设备"
- AI 判定 NG 须经质检员**人工复核**（`/inspections/{id}/review`），确认缺陷/误报全程留痕
- 遥测摄入带**卡死传感器校验**（整批恒定值拒绝写入），防止垃圾数据喂给 RUL 模型

## 边缘数据采集（OPC UA → MQTT → 遥测管线，M1）

```
S7-1500 (TIA V20/SICAR, OPC UA 服务器, 只读订阅)
   │ opc.tcp://<PLC>:4840  (Basic256Sha256, 证书互信)
   ▼
edge_collector（产线 IPC 容器）
   周期批量读 tags_weld.yaml 声明的标签 → cycle 序号 → MQTT QoS1
   断连时落 SQLite，恢复后按原始顺序补传
   │ line03/opmode7-em040/{device_id}/telemetry
   ▼
mqtt-broker（Mosquitto，产线内网）
   ▼
telemetry_consumer
   冻结去重（防服务端卡死传感器整批拒绝）→ 批量写入
   ├─ http sink（生产）: 服务账号 JWT → POST /api/v1/devices/{id}/telemetry
   │                    （复用卡死校验 + 幂等去重 + 班组长鉴权）
   └─ db sink（联调）:   直写 device_telemetry，按 (device_id, cycle) 幂等
   ▼
device_telemetry → RUL sidecar → 虚焊预警工单（与种子/仿真数据同构，无缝替换）
```

标签映射表 `services/edge_collector/tags_weld.yaml` 是边缘采集与 PLC 程序的**接口契约**：
特征名与 `device_telemetry.readings`/`seed_weld_line` 完全一致，遥测数据从仿真切到真实 PLC 时
AI 链路零改动。

**OPC UA 仿真服务器（无 PLC/无 PLCSIM Advanced 时验证生产代码路径）**：

```bash
# S7-1500 风格服务器：ns=3 符号节点 + 实时值 + Basic256Sha256 加密 + 证书互信
python -m services.edge_collector.certs          # 生成证书三件套（一次性）
python -m services.edge_collector.opcua_server_sim --port 4840 --secure

# 生产采集器直连仿真服务器（与连真机完全同码路径）
python -m services.edge_collector.app \
    --endpoint opc.tcp://127.0.0.1:4840/line03/ --mqtt-host 127.0.0.1
```

TIA Portal 侧启用 OPC UA 服务器、证书互换、UaExpert 冻结标签表的完整手册：
`deployment/tia_opcua_setup.md`（GUI 精确路径）+ `deployment/tia_openness_opcua.ps1`（Openness 只读巡检）。

**本机仿真联调（无 OPC UA，纯数据链）**：

```bash
# 终端1: broker（或 docker compose up mqtt-broker）
mosquitto -c deployment/mosquitto.conf

# 终端2: 消费者（db sink 直写本地库）
python -m services.telemetry_consumer.app --sink db

# 终端3: 仿真采集器（播放退化曲线，与 RUL 训练同来源）
python -m services.edge_collector.app --simulate --max-cycles 300

# 验证：设备遥测已写入
curl -X POST http://localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"shift_lead_wang","password":"<DEMO_PASSWORD>"}'
curl "http://localhost:8000/api/v1/devices/line03-weld-laser-01/telemetry?limit=5" \
  -H "Authorization: Bearer <token>"
```

**接入真实 S7-1500（五步）**：

1. **TIA 侧**：CPU 属性 → OPC UA → 启用服务器；运行系统许可证勾选 OPC UA；
   安全策略选 Basic256Sha256_SignAndEncrypt；把 `services/edge_collector/certs/` 的
   客户端证书导入"受信客户端"，导出 PLC 服务器证书到同目录
2. **浏览确认节点**：用 UaExpert 连接 `opc.tcp://<PLC IP>:4840`（ns=3 符号命名空间），
   核对 `tags_weld.yaml` 中所有 `TODO(TIA)` 注释的路径；确认后冻结该表进仓库
3. **服务账号**：用管理端创建班组长及以上角色账号（如 `telemetry_bot`），
   密码写入 `.env` 的 `TELEMETRY_USER/TELEMETRY_PASSWORD`
4. **启动链路**：`docker compose up -d mqtt-broker telemetry-consumer backend`，
   边缘 IPC 上 `docker compose up -d edge-collector`（命令去掉 `--simulate`，
   换 `--endpoint/--user/--password`）
5. **验收**：`GET /api/v1/devices` 应看到 4 台焊接设备遥测行数持续增长；
   `GET /api/v1/devices/{id}` 返回最新 RUL 预测 → 低于设备阈值自动建预防性工单

集成测试：`tests/integration/test_edge_e2e.py`（进程内 amqtt broker，全链路无外部依赖）

## License

MIT
