"""
models.py — SQLAlchemy ORM 模型
===============================
Phase 2: SQLite (开发)
Phase 3: PostgreSQL (生产, 模型不变, 只改连接串)

13 张表:
  users, documents, ingestion_transactions, dedup_log,
  work_orders, knowledge_cases,
  conversations, token_usage_daily,
  audit_logs, sync_log,
  device_telemetry, inspection_records, rul_predictions,   ← ML 融合新增
  devices, inspection_products                             ← 工业场景化新增（台账/换型）
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, Date,
    ForeignKey, JSON, BigInteger, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from src.core.database import Base


def _uuid():
    return str(uuid.uuid4())


def _now():
    return datetime.now().replace(microsecond=0)


# ── 用户与权限 ─────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_uuid)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(128), default="")
    display_name = Column(String(100), nullable=False)
    role = Column(String(30), nullable=False)
    workshop_id = Column(String(50), default="")
    phone = Column(String(20), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index("idx_users_role", "role"),
        Index("idx_users_workshop", "workshop_id"),
    )


# ── 文档与知识库 ───────────────────────────────

class Document(Base):
    """文档元数据表。"""
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=_uuid)
    doc_id = Column(String(100), unique=True, nullable=False)
    title = Column(String(500), nullable=False)
    doc_type = Column(String(50), nullable=False)
    file_format = Column(String(20), default="")
    file_path = Column(Text, default="")
    workshop_id = Column(String(50), default="")
    equipment_model = Column(String(200), default="")
    classification = Column(String(20), default="internal")
    version = Column(String(20), default="1.0")
    status = Column(String(20), default="active")
    chunk_count = Column(Integer, default=0)
    file_size_bytes = Column(BigInteger, default=0)
    ingested_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (
        Index("idx_docs_type", "doc_type"),
        Index("idx_docs_status", "status"),
        Index("idx_docs_workshop", "workshop_id"),
        Index("idx_docs_equipment", "equipment_model"),
    )


class IngestionTransaction(Base):
    """双写事务记录表。"""
    __tablename__ = "ingestion_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(100), nullable=False)
    chunk_id = Column(String(100), unique=True, nullable=False)
    milvus_ok = Column(Boolean, default=False)
    es_ok = Column(Boolean, default=False)
    status = Column(String(20), default="pending")
    retry_count = Column(Integer, default=0)
    last_error = Column(Text, default="")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (Index("idx_ingest_status", "status", "created_at"),)


class DedupLog(Base):
    """离线同步去重表。"""
    __tablename__ = "dedup_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    offline_uuid = Column(String(36), unique=True, nullable=False)
    online_uuid = Column(String(36), nullable=True)
    similarity_score = Column(Float, nullable=True)
    action = Column(String(20), nullable=False)
    reviewer = Column(String(100), default="")
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)


# ── 工单 ──────────────────────────────────────

class WorkOrder(Base):
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mes_event_id = Column(String(100), unique=True, nullable=False)
    mes_order_id = Column(String(100), default="")
    equipment_id = Column(String(100), default="")
    equipment_model = Column(String(200), default="")
    fault_code = Column(String(50), default="")
    fault_desc = Column(Text, default="")
    priority = Column(String(10), default="P2")
    status = Column(String(20), default="pending")
    assigned_to = Column(String(36), nullable=True)
    workshop_id = Column(String(50), default="")
    diagnosis = Column(Text, default="")
    repair_actions = Column(Text, default="")
    resolution = Column(Text, default="")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_wo_status", "status"),
        Index("idx_wo_workshop", "workshop_id"),
        Index("idx_wo_equipment", "equipment_id"),
        Index("idx_wo_created", "created_at"),
    )


# ── 知识案例 ──────────────────────────────────

class KnowledgeCase(Base):
    __tablename__ = "knowledge_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(100), unique=True, nullable=False)
    equipment_model = Column(String(200), default="")
    fault_code = Column(String(50), default="")
    fault_category = Column(String(100), default="")
    root_cause = Column(Text, default="")
    repair_steps = Column(JSON, default=list)
    safety_notes = Column(Text, default="")
    source_wo_id = Column(String(100), default="")
    verification = Column(String(20), default="unverified")
    weight = Column(Float, default=1.0)
    status = Column(String(20), default="active")
    review_status = Column(String(20), default="")
    reviewer_id = Column(String(36), nullable=True)
    access_count = Column(Integer, default=0)
    last_accessed = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index("idx_case_model", "equipment_model"),
        Index("idx_case_fault", "fault_code"),
        Index("idx_case_category", "fault_category"),
        Index("idx_case_status", "status"),
    )


# ── 对话与记忆 ────────────────────────────────

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=_uuid)
    session_id = Column(String(100), nullable=False)
    user_id = Column(String(36), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    citations = Column(JSON, default=list)
    token_count = Column(Integer, default=0)
    model_used = Column(String(50), default="")
    model_cost = Column(Float, default=0.0)
    latency_ms = Column(Integer, default=0)
    feedback = Column(String(10), default="")
    created_at = Column(DateTime, default=_now)

    __table_args__ = (
        Index("idx_conv_session", "session_id", "created_at"),
        Index("idx_conv_user", "user_id", "created_at"),
    )


class TokenUsageDaily(Base):
    __tablename__ = "token_usage_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    workshop_id = Column(String(50), nullable=False)
    agent = Column(String(50), nullable=False)
    model = Column(String(50), nullable=False)
    prompt_tokens = Column(BigInteger, default=0)
    completion_tokens = Column(BigInteger, default=0)
    cost = Column(Float, default=0.0)
    request_count = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("date", "workshop_id", "agent", "model", name="uq_token_daily"),
        Index("idx_token_date", "date"),
    )


# ── 审计与监控 ────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False)
    user_id = Column(String(36), default="")
    agent_id = Column(String(50), default="")
    tool_name = Column(String(100), default="")
    target_table = Column(String(100), default="")
    severity = Column(String(20), default="info")
    detail = Column(JSON, default=dict)
    ip_address = Column(String(45), default="")
    created_at = Column(DateTime, default=_now)

    __table_args__ = (
        Index("idx_audit_type", "event_type", "created_at"),
        Index("idx_audit_severity", "severity", "created_at"),
    )


class SyncLog(Base):
    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sync_type = Column(String(50), nullable=False)
    direction = Column(String(20), nullable=False)
    records_synced = Column(Integer, default=0)
    status = Column(String(20), default="success")
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)


# ── ML 融合新增 ────────────────────────────────

class DeviceTelemetry(Base):
    """设备传感器遥测（预测性维护节点的输入）。

    readings 为 JSON 对象：单次循环的全部传感器值（如 C-MAPSS 的 op1-3/s01-21），
    具体特征选择由 RUL 服务按 scaler.feature_cols 决定。
    """

    __tablename__ = "device_telemetry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(100), nullable=False)
    cycle = Column(Integer, nullable=False)          # 循环序号（单调递增）
    readings = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (
        Index("idx_telemetry_device_cycle", "device_id", "cycle"),
    )


class InspectionRecord(Base):
    """视觉质检记录：每张过检图片的检测结果与热力图（可追溯）。

    review_* 字段是人在回路的关键：AI 判定必须经质检员复核
    （confirmed_defect=确认缺陷 / false_positive=误报），误报率统计反哺阈值校准。
    """

    __tablename__ = "inspection_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    image_path = Column(Text, default="")
    image_url = Column(Text, default="")
    model = Column(String(50), default="patchcore")
    product_code = Column(String(50), default="")
    station_id = Column(String(50), default="")
    defect_detected = Column(Boolean, default=False)
    anomaly_score = Column(Float, default=0.0)
    threshold = Column(Float, nullable=True)
    heatmap_path = Column(Text, default="")
    work_order_id = Column(String(100), nullable=True)
    review_result = Column(String(20), default="")        # "" / confirmed_defect / false_positive
    reviewed_by = Column(String(36), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    workshop_id = Column(String(50), default="")
    created_by = Column(String(36), default="")
    created_at = Column(DateTime, default=_now)

    __table_args__ = (
        Index("idx_insp_created", "created_at"),
        Index("idx_insp_workshop", "workshop_id"),
        Index("idx_insp_review", "review_result"),
    )


class RULPrediction(Base):
    """RUL 预测历史：每次预测的设备、结果与模型版本（审计回放用）。"""

    __tablename__ = "rul_predictions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(100), nullable=False)
    rul_cycles = Column(Float, default=0.0)
    model = Column(String(50), default="lstm")
    window_size = Column(Integer, default=0)
    n_readings = Column(Integer, default=0)
    padded = Column(Boolean, default=False)
    health_score = Column(Float, nullable=True)      # 业务化输出：0-100 设备健康分
    estimated_days = Column(Float, nullable=True)    # 业务化输出：预计剩余天数
    predicted_at = Column(DateTime, default=_now)

    __table_args__ = (
        Index("idx_rulp_device", "device_id", "predicted_at"),
    )


# ── 工业场景化新增：设备台账 & 质检换型 ─────────────


class Device(Base):
    """设备台账：机台的静态档案（PdM 业务翻译的依据）。

    RUL 预测只有结合台账才有业务意义：
    - cycles_per_day  → 把"剩余循环"换算成"预计剩余天数"
    - critical_component → 维修工单的对象（换轴承而不是"修设备"）
    - rul_warn_cycles  → 设备级预警阈值（不同关键度设备阈值不同，覆盖全局默认）
    """

    __tablename__ = "devices"

    id = Column(String(36), primary_key=True, default=_uuid)
    device_id = Column(String(100), unique=True, nullable=False)  # 与 device_telemetry 关联
    name = Column(String(200), nullable=False)                    # 现场称呼: 1号激光焊机
    equipment_model = Column(String(200), default="")
    line = Column(String(100), default="")                        # 所在产线
    workshop_id = Column(String(50), default="")
    critical_component = Column(String(200), default="")          # 关键部件: 泵浦二极管/保护镜
    commissioning_date = Column(DateTime, nullable=True)
    cycles_per_day = Column(Float, default=24.0)                  # 每天运行循环数（换算剩余天数）
    rul_warn_cycles = Column(Integer, default=50)                 # 设备级预警阈值
    status = Column(String(20), default="running")                # running / maintenance / retired

    __table_args__ = (
        Index("idx_device_workshop", "workshop_id"),
    )


class InspectionProduct(Base):
    """质检换型映射：产品 → 检测模型 + 判定阈值。

    真实产线多品类共线，不同产品的检测模型和判定阈值不同
    （漏检成本高的产品阈值更保守）。新增品类：插入一行 + 训练对应模型即可，
    无需改代码。
    """

    __tablename__ = "inspection_products"

    product_code = Column(String(50), primary_key=True)           # bottle / 客户料号
    product_name = Column(String(200), default="")
    model_name = Column(String(50), default="patchcore")          # patchcore / efficientad
    threshold = Column(Float, nullable=True)                      # 覆盖模型默认校准阈值
    station_id = Column(String(50), default="")                   # 质检工位
    workshop_id = Column(String(50), default="")
    enabled = Column(Boolean, default=True)
