from pydantic import BaseModel, Field, field_validator
from enum import Enum
from typing import Literal
from datetime import datetime
import uuid


class TicketStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    ESCALATED = "escalated"


class RefundReason(str, Enum):
    DAMAGED = "damaged"
    WRONG_ITEM = "wrong_item"
    NOT_RECEIVED = "not_received"
    QUALITY_ISSUE = "quality_issue"
    OTHER = "other"


# ============================================================
# Request/Response Schemas
# ============================================================

class ChatRequest(BaseModel):
    """前端发送的聊天请求"""
    messages: list[dict]
    thread_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str | None = None   # 端到端追踪 ID，由前端生成
    user_role: str | None = None
    user_id: str | None = None


class ResumeRequest(BaseModel):
    """人工审批操作 — 恢复 LangGraph Checkpoint"""
    thread_id: str
    action: Literal["approve", "reject"]
    reviewer_id: str
    reviewer_role: str = "AGENT"  # 前端传入当前用户角色，后端二次校验
    comment: str = ""


class TicketCreateRequest(BaseModel):
    order_id: str
    reason: RefundReason
    description: str = Field(min_length=1, max_length=1000)
    user_id: str


class TicketResponse(BaseModel):
    id: str
    order_id: str
    user_id: str
    reason: RefundReason
    description: str
    status: TicketStatus
    amount: float
    risk_score: int = Field(ge=0, le=100)
    thread_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# Order Schemas (工具调用返回的数据结构)
# ============================================================

class OrderItem(BaseModel):
    id: str
    name: str
    image_url: str = ""
    quantity: int = Field(ge=1)
    price: float = Field(ge=0)


class OrderDetail(BaseModel):
    """get_order_detail() 工具的返回值"""
    id: str
    user_id: str
    status: str
    items: list[OrderItem]
    total_amount: float = Field(ge=0)
    shipping_address: str
    created_at: str
    tracking_number: str | None = None
    carrier: str | None = None

    @field_validator("total_amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        return round(v, 2)


# ============================================================
# Risk Check Schemas
# ============================================================

class RiskCheckResult(BaseModel):
    """check_risk_level() 工具的返回值"""
    risk_score: int = Field(ge=0, le=100)
    risk_level: Literal["low", "medium", "high"]
    reasons: list[str]
    auto_approve: bool
    threshold: float
    recommendation: str


# ============================================================
# Refund Schemas
# ============================================================

class RefundResult(BaseModel):
    """execute_refund() 工具的返回值"""
    success: bool
    refund_id: str
    amount: float
    estimated_days: int
    message: str


# ============================================================
# Notification Schemas
# ============================================================

class NotificationResult(BaseModel):
    """send_notification() 工具的返回值"""
    success: bool
    email_id: str
    to: str
    subject: str
    sent_at: str
