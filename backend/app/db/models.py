from datetime import datetime
from typing import List, Optional
from sqlalchemy import String, Integer, Float, DateTime, JSON, ForeignKey, Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base
import enum

class UserRole(str, enum.Enum):
    USER = "USER"
    AGENT = "AGENT"
    MANAGER = "MANAGER"

class TicketStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(100), unique=True)
    role: Mapped[UserRole] = mapped_column(SqlEnum(UserRole), default=UserRole.USER)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    orders: Mapped[List["Order"]] = relationship(back_populates="user")
    tickets: Mapped[List["Ticket"]] = relationship(back_populates="requester", foreign_keys="[Ticket.requester_id]")
    approved_tickets: Mapped[List["Ticket"]] = relationship(back_populates="operator", foreign_keys="[Ticket.operator_id]")

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))
    items: Mapped[dict] = mapped_column(JSON)
    shipping_address: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="orders")
    tickets: Mapped[List["Ticket"]] = relationship(back_populates="order")

class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    thread_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[TicketStatus] = mapped_column(SqlEnum(TicketStatus), default=TicketStatus.PENDING)
    reason: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    order: Mapped["Order"] = relationship(back_populates="tickets")
    requester: Mapped["User"] = relationship(back_populates="tickets", foreign_keys=[requester_id])
    # 谁审批的这个工单（可能为空，因为刚创建时还没人审批）
    operator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    # 对应的关系映射
    operator: Mapped[Optional["User"]] = relationship(back_populates="approved_tickets", foreign_keys=[operator_id])
    refund_log: Mapped[Optional["RefundLog"]] = relationship(back_populates="ticket")

class RefundLog(Base):
    __tablename__ = "refund_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    refund_id: Mapped[str] = mapped_column(String(100), unique=True)
    amount: Mapped[float] = mapped_column(Float)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    ticket: Mapped["Ticket"] = relationship(back_populates="refund_log")

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(100), index=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    node_name: Mapped[str] = mapped_column(String(50))
    event_type: Mapped[str] = mapped_column(String(50))
    input_data: Mapped[Optional[dict]] = mapped_column(JSON)
    output_data: Mapped[Optional[dict]] = mapped_column(JSON)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    success: Mapped[Optional[bool]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserMemory(Base):
    """
    用户跨会话记忆表 — 持久化风控相关的用户画像

    - 每个用户一条记录（unique on user_id）
    - 每次退款完成或审批拒绝后由相应节点 upsert
    - risk_check_node 读取此表来补充实时风控评分
    """
    __tablename__ = "user_memory"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    # 累计退款次数（已完成）
    refund_count: Mapped[int] = mapped_column(Integer, default=0)
    # 累计被拒绝次数
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    # 是否被标记为欺诈风险（手动 / 自动触发）
    fraud_flag: Mapped[bool] = mapped_column(default=False)
    # 最后一次退款时间（用于检测高频短时间退款）
    last_refund_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 自由文本备注（供人工审核员填写）
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped["User"] = relationship()
