from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from app.db.vector_type import Vector
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base
from app.db.tenant_context import current_tenant_id
import enum

class UserRole(str, enum.Enum):
    USER = "USER"
    AGENT = "AGENT"
    MANAGER = "MANAGER"
    FINANCE = "FINANCE"
    SECURITY = "SECURITY"


class ErpDocumentStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    POSTED = "POSTED"
    CANCELLED = "CANCELLED"


class ErpPaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    CAPTURED = "CAPTURED"
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"


class ErpFulfillmentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PICKED = "PICKED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    RETURNED = "RETURNED"


class ErpRefundStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    POLICY_CHECKED = "POLICY_CHECKED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED"


class ErpLedgerEntryType(str, enum.Enum):
    SALE = "SALE"
    PAYMENT_CAPTURE = "PAYMENT_CAPTURE"
    REFUND = "REFUND"
    INVENTORY_ADJUSTMENT = "INVENTORY_ADJUSTMENT"


class ErpSupportTicketStatus(str, enum.Enum):
    OPEN = "OPEN"
    TRIAGED = "TRIAGED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


class ErpReturnStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    AUTHORIZED = "AUTHORIZED"
    RECEIVED = "RECEIVED"
    INSPECTED = "INSPECTED"
    RESTOCKED = "RESTOCKED"
    REJECTED = "REJECTED"


class ErpInspectionResult(str, enum.Enum):
    PASS = "PASS"
    DAMAGED = "DAMAGED"
    MISSING_PARTS = "MISSING_PARTS"
    NOT_RECEIVED = "NOT_RECEIVED"


class ErpInventoryMovementType(str, enum.Enum):
    RESERVE = "RESERVE"
    PICK = "PICK"
    SHIP = "SHIP"
    RETURN_RECEIPT = "RETURN_RECEIPT"
    RESTOCK = "RESTOCK"
    ADJUSTMENT = "ADJUSTMENT"


class ErpJournalStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    POSTED = "POSTED"
    REVERSED = "REVERSED"


class ErpPolicyStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class ErpPartnerType(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    EMPLOYEE = "EMPLOYEE"
    BANK = "BANK"
    INTERCOMPANY = "INTERCOMPANY"


class ErpFiscalPeriodStatus(str, enum.Enum):
    OPEN = "OPEN"
    SOFT_CLOSED = "SOFT_CLOSED"
    CLOSED = "CLOSED"


class ErpProcurementStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ORDERED = "ORDERED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    INVOICED = "INVOICED"
    CANCELLED = "CANCELLED"


class ErpGoodsReceiptStatus(str, enum.Enum):
    EXPECTED = "EXPECTED"
    RECEIVED = "RECEIVED"
    QUALITY_HOLD = "QUALITY_HOLD"
    POSTED = "POSTED"


class ErpApInvoiceStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    MATCHED = "MATCHED"
    APPROVED = "APPROVED"
    POSTED = "POSTED"
    PAID = "PAID"
    BLOCKED = "BLOCKED"


class ErpReservationStatus(str, enum.Enum):
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class ErpBomStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class ErpWorkOrderStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    RELEASED = "RELEASED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ErpAssetStatus(str, enum.Enum):
    CAPITALIZED = "CAPITALIZED"
    IN_SERVICE = "IN_SERVICE"
    IMPAIRED = "IMPAIRED"
    RETIRED = "RETIRED"


class ErpConsolidationStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    POSTED = "POSTED"
    FAILED = "FAILED"


class ErpCompensationStatus(str, enum.Enum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ErpBusinessRequestStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class ErpMasterDataRequestStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"


class ErpOutboxStatus(str, enum.Enum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class ErpConnectorStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ERROR = "ERROR"
    DRAFT = "DRAFT"


class ErpIdempotencyStatus(str, enum.Enum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ErpSagaStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPENSATING = "COMPENSATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class ErpSagaStepStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TenantScopedMixin:
    """Attach an explicit tenant boundary to independently queryable ERP rows."""

    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)

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
    customer_profile: Mapped[Optional["CustomerProfile"]] = relationship(back_populates="user")
    employee_profile: Mapped[Optional["EmployeeProfile"]] = relationship(back_populates="user")

class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_orders_amount_non_negative"),
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "external_order_id",
            name="uq_orders_tenant_source_external",
        ),
        Index("ix_orders_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    source_system: Mapped[str] = mapped_column(String(50), default="MINI_ERP", index=True)
    external_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    status: Mapped[str] = mapped_column(String(20))
    items: Mapped[dict] = mapped_column(JSON)
    shipping_address: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="orders")
    tickets: Mapped[List["Ticket"]] = relationship(back_populates="order")
    erp_lines: Mapped[List["ErpOrderLine"]] = relationship(back_populates="order")
    erp_payments: Mapped[List["PaymentTransaction"]] = relationship(back_populates="order")
    erp_invoices: Mapped[List["InvoiceDocument"]] = relationship(back_populates="order")
    erp_shipments: Mapped[List["ShipmentDocument"]] = relationship(back_populates="order")
    erp_refund_requests: Mapped[List["RefundRequest"]] = relationship(back_populates="order")


class BusinessObjectAlias(Base):
    """Maps source-system identifiers to one canonical business object ID."""

    __tablename__ = "erp_business_object_aliases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "object_type",
            "source_system",
            "external_id",
            name="uq_erp_alias_source_external",
        ),
        Index(
            "ix_erp_alias_canonical_lookup",
            "tenant_id",
            "object_type",
            "canonical_id",
        ),
    )

    alias_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    object_type: Mapped[str] = mapped_column(String(50), index=True)
    source_system: Mapped[str] = mapped_column(String(50), index=True)
    external_id: Mapped[str] = mapped_column(String(120), index=True)
    canonical_id: Mapped[str] = mapped_column(String(120), index=True)
    alias_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TenantOrganization(Base):
    __tablename__ = "erp_tenant_organizations"

    tenant_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    industry: Mapped[str] = mapped_column(String(80), default="ecommerce")
    region: Mapped[str] = mapped_column(String(60), default="CN")
    plan: Mapped[str] = mapped_column(String(40), default="demo")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BusinessUnit(Base):
    __tablename__ = "erp_business_units"

    business_unit_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("erp_tenant_organizations.tenant_id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(60), default="CN-East")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Department(TenantScopedMixin, Base):
    __tablename__ = "erp_departments"

    department_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    business_unit_id: Mapped[str] = mapped_column(ForeignKey("erp_business_units.business_unit_id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    manager_employee_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CostCenter(TenantScopedMixin, Base):
    __tablename__ = "erp_cost_centers"

    cost_center_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    department_id: Mapped[str] = mapped_column(ForeignKey("erp_departments.department_id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    budget_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomerProfile(TenantScopedMixin, Base):
    __tablename__ = "erp_customer_profiles"

    customer_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    segment: Mapped[str] = mapped_column(String(40), default="standard")
    region: Mapped[str] = mapped_column(String(60), default="CN-East")
    lifetime_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    refund_count_90d: Mapped[int] = mapped_column(Integer, default=0)
    complaint_count_90d: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="customer_profile")


class EmployeeProfile(TenantScopedMixin, Base):
    __tablename__ = "erp_employee_profiles"

    employee_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    department: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(80))
    manager_employee_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    cost_center: Mapped[str] = mapped_column(String(50), default="CC-GENERAL")
    approval_limit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="employee_profile")


class ProductCatalog(TenantScopedMixin, Base):
    __tablename__ = "erp_products"

    product_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(80))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    returnable: Mapped[bool] = mapped_column(Boolean, default=True)
    warranty_days: Mapped[int] = mapped_column(Integer, default=7)
    supplier_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    attributes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    order_lines: Mapped[List["ErpOrderLine"]] = relationship(back_populates="product")
    inventory_items: Mapped[List["InventoryItem"]] = relationship(back_populates="product")


class Warehouse(TenantScopedMixin, Base):
    __tablename__ = "erp_warehouses"

    warehouse_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(60))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    inventory_items: Mapped[List["InventoryItem"]] = relationship(back_populates="warehouse")


class InventoryItem(TenantScopedMixin, Base):
    __tablename__ = "erp_inventory_items"

    inventory_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("erp_warehouses.warehouse_id"), index=True)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    quantity_reserved: Mapped[int] = mapped_column(Integer, default=0)
    reorder_point: Mapped[int] = mapped_column(Integer, default=10)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product: Mapped["ProductCatalog"] = relationship(back_populates="inventory_items")
    warehouse: Mapped["Warehouse"] = relationship(back_populates="inventory_items")


class ErpOrderLine(TenantScopedMixin, Base):
    __tablename__ = "erp_order_lines"

    line_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    sku: Mapped[str] = mapped_column(String(80))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    fulfillment_status: Mapped[ErpFulfillmentStatus] = mapped_column(
        SqlEnum(ErpFulfillmentStatus),
        default=ErpFulfillmentStatus.PENDING,
    )
    returnable: Mapped[bool] = mapped_column(Boolean, default=True)

    order: Mapped["Order"] = relationship(back_populates="erp_lines")
    product: Mapped["ProductCatalog"] = relationship(back_populates="order_lines")


class PaymentTransaction(TenantScopedMixin, Base):
    __tablename__ = "erp_payment_transactions"
    __table_args__ = (CheckConstraint("amount >= 0", name="ck_erp_payments_amount_non_negative"),)

    payment_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50), default="mockpay")
    method: Mapped[str] = mapped_column(String(40), default="card")
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    status: Mapped[ErpPaymentStatus] = mapped_column(SqlEnum(ErpPaymentStatus), default=ErpPaymentStatus.PENDING)
    captured_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    external_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    order: Mapped["Order"] = relationship(back_populates="erp_payments")


class InvoiceDocument(TenantScopedMixin, Base):
    __tablename__ = "erp_invoices"
    __table_args__ = (CheckConstraint("amount >= 0", name="ck_erp_invoices_amount_non_negative"),)

    invoice_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    status: Mapped[ErpDocumentStatus] = mapped_column(SqlEnum(ErpDocumentStatus), default=ErpDocumentStatus.POSTED)
    tax_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    order: Mapped["Order"] = relationship(back_populates="erp_invoices")


class ShipmentDocument(TenantScopedMixin, Base):
    __tablename__ = "erp_shipments"

    shipment_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("erp_warehouses.warehouse_id"), index=True)
    carrier: Mapped[str] = mapped_column(String(60))
    tracking_number: Mapped[str] = mapped_column(String(100))
    status: Mapped[ErpFulfillmentStatus] = mapped_column(SqlEnum(ErpFulfillmentStatus), default=ErpFulfillmentStatus.PENDING)
    shipped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="erp_shipments")
    warehouse: Mapped["Warehouse"] = relationship()


class RefundRequest(Base):
    __tablename__ = "erp_refund_requests"
    __table_args__ = (
        CheckConstraint("requested_amount >= 0", name="ck_erp_refunds_requested_non_negative"),
        CheckConstraint(
            "approved_amount IS NULL OR approved_amount >= 0",
            name="ck_erp_refunds_approved_non_negative",
        ),
        CheckConstraint(
            "approved_amount IS NULL OR approved_amount <= requested_amount",
            name="ck_erp_refunds_approved_lte_requested",
        ),
    )

    refund_request_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("erp_customer_profiles.customer_id"), index=True)
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    approved_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    reason_code: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(500))
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    status: Mapped[ErpRefundStatus] = mapped_column(SqlEnum(ErpRefundStatus), default=ErpRefundStatus.REQUESTED)
    policy_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    duplicate_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    order: Mapped["Order"] = relationship(back_populates="erp_refund_requests")
    customer: Mapped["CustomerProfile"] = relationship()
    approvals: Mapped[List["BusinessApprovalRecord"]] = relationship(back_populates="refund_request")
    ledger_entries: Mapped[List["FinancialLedgerEntry"]] = relationship(back_populates="refund_request")


class SupportTicket(Base):
    __tablename__ = "erp_support_tickets"

    support_ticket_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("erp_tenant_organizations.tenant_id"), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("erp_customer_profiles.customer_id"), index=True)
    channel: Mapped[str] = mapped_column(String(50), default="web")
    subject: Mapped[str] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(String(1000))
    intent: Mapped[str] = mapped_column(String(80), default="refund")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    sla_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    assigned_employee_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[ErpSupportTicketStatus] = mapped_column(
        SqlEnum(ErpSupportTicketStatus),
        default=ErpSupportTicketStatus.OPEN,
    )
    resolution_summary: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    order: Mapped[Optional["Order"]] = relationship()
    customer: Mapped["CustomerProfile"] = relationship()


class CustomerComplaint(TenantScopedMixin, Base):
    __tablename__ = "erp_customer_complaints"

    complaint_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    support_ticket_id: Mapped[str] = mapped_column(ForeignKey("erp_support_tickets.support_ticket_id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("erp_customer_profiles.customer_id"), index=True)
    complaint_type: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    sentiment: Mapped[str] = mapped_column(String(30), default="neutral")
    escalation_required: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    support_ticket: Mapped["SupportTicket"] = relationship()
    customer: Mapped["CustomerProfile"] = relationship()


class ReturnAuthorization(TenantScopedMixin, Base):
    __tablename__ = "erp_return_authorizations"

    rma_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    refund_request_id: Mapped[str] = mapped_column(ForeignKey("erp_refund_requests.refund_request_id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("erp_warehouses.warehouse_id"), index=True)
    status: Mapped[ErpReturnStatus] = mapped_column(SqlEnum(ErpReturnStatus), default=ErpReturnStatus.REQUESTED)
    return_label_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    refund_request: Mapped["RefundRequest"] = relationship()
    order: Mapped["Order"] = relationship()
    warehouse: Mapped["Warehouse"] = relationship()


class ReturnInspection(TenantScopedMixin, Base):
    __tablename__ = "erp_return_inspections"

    inspection_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    rma_id: Mapped[str] = mapped_column(ForeignKey("erp_return_authorizations.rma_id"), index=True)
    inspector_employee_id: Mapped[str] = mapped_column(String(50))
    result: Mapped[ErpInspectionResult] = mapped_column(SqlEnum(ErpInspectionResult), default=ErpInspectionResult.PASS)
    restockable: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    inspected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    return_authorization: Mapped["ReturnAuthorization"] = relationship()


class InventoryMovement(TenantScopedMixin, Base):
    __tablename__ = "erp_inventory_movements"

    movement_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("erp_warehouses.warehouse_id"), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    rma_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_return_authorizations.rma_id"), nullable=True, index=True)
    movement_type: Mapped[ErpInventoryMovementType] = mapped_column(SqlEnum(ErpInventoryMovementType))
    quantity_delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(160))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    product: Mapped["ProductCatalog"] = relationship()
    warehouse: Mapped["Warehouse"] = relationship()
    order: Mapped[Optional["Order"]] = relationship()
    return_authorization: Mapped[Optional["ReturnAuthorization"]] = relationship()


class JournalEntry(Base):
    __tablename__ = "erp_journal_entries"
    __table_args__ = (
        CheckConstraint("total_debit >= 0", name="ck_erp_journal_debit_non_negative"),
        CheckConstraint("total_credit >= 0", name="ck_erp_journal_credit_non_negative"),
        CheckConstraint("total_debit = total_credit", name="ck_erp_journal_balanced"),
    )

    journal_entry_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("erp_tenant_organizations.tenant_id"), index=True)
    source_document: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(String(300))
    status: Mapped[ErpJournalStatus] = mapped_column(SqlEnum(ErpJournalStatus), default=ErpJournalStatus.POSTED)
    total_debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    total_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class JournalLine(TenantScopedMixin, Base):
    __tablename__ = "erp_journal_lines"
    __table_args__ = (
        CheckConstraint("debit >= 0", name="ck_erp_journal_lines_debit_non_negative"),
        CheckConstraint("credit >= 0", name="ck_erp_journal_lines_credit_non_negative"),
        CheckConstraint("NOT (debit > 0 AND credit > 0)", name="ck_erp_journal_lines_single_side"),
    )

    journal_line_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    journal_entry_id: Mapped[str] = mapped_column(ForeignKey("erp_journal_entries.journal_entry_id"), index=True)
    account_code: Mapped[str] = mapped_column(String(80))
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    cost_center_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_cost_centers.cost_center_id"), nullable=True)
    memo: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    journal_entry: Mapped["JournalEntry"] = relationship()
    cost_center: Mapped[Optional["CostCenter"]] = relationship()


class PolicyVersion(Base):
    __tablename__ = "erp_policy_versions"

    policy_version_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("erp_tenant_organizations.tenant_id"), index=True)
    policy_id: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(160))
    scenario: Mapped[str] = mapped_column(String(80), index=True)
    content: Mapped[str] = mapped_column(String(3000))
    status: Mapped[ErpPolicyStatus] = mapped_column(SqlEnum(ErpPolicyStatus), default=ErpPolicyStatus.ACTIVE)
    effective_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    effective_to: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    owner_department_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


class ApprovalMatrixRule(Base):
    __tablename__ = "erp_approval_matrix_rules"

    approval_rule_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("erp_tenant_organizations.tenant_id"), index=True)
    scenario: Mapped[str] = mapped_column(String(80), index=True)
    stage_id: Mapped[str] = mapped_column(String(80))
    min_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    max_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    required_role: Mapped[str] = mapped_column(String(50))
    required_department_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    escalation_after_hours: Mapped[int] = mapped_column(Integer, default=24)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DataQualityIssue(Base):
    __tablename__ = "erp_data_quality_issues"

    issue_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("erp_tenant_organizations.tenant_id"), index=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    object_id: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    issue_type: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(500))
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class BusinessPartner(Base):
    __tablename__ = "erp_business_partners"

    partner_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("erp_tenant_organizations.tenant_id"), index=True)
    partner_type: Mapped[ErpPartnerType] = mapped_column(SqlEnum(ErpPartnerType), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    legal_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    country: Mapped[str] = mapped_column(String(40), default="CN")
    tax_registration_number: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    risk_rating: Mapped[str] = mapped_column(String(30), default="normal")
    payment_terms: Mapped[str] = mapped_column(String(50), default="immediate")
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CompanyCode(Base):
    __tablename__ = "erp_company_codes"

    company_code_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("erp_tenant_organizations.tenant_id"), index=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    legal_name: Mapped[str] = mapped_column(String(180))
    country: Mapped[str] = mapped_column(String(40), default="CN")
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    fiscal_variant: Mapped[str] = mapped_column(String(20), default="K4")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SalesOrganization(TenantScopedMixin, Base):
    __tablename__ = "erp_sales_organizations"

    sales_org_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    company_code_id: Mapped[str] = mapped_column(ForeignKey("erp_company_codes.company_code_id"), index=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(140))
    distribution_channel: Mapped[str] = mapped_column(String(60), default="online")
    division: Mapped[str] = mapped_column(String(60), default="consumer")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Plant(TenantScopedMixin, Base):
    __tablename__ = "erp_plants"

    plant_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    company_code_id: Mapped[str] = mapped_column(ForeignKey("erp_company_codes.company_code_id"), index=True)
    sales_org_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_sales_organizations.sales_org_id"), nullable=True)
    warehouse_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_warehouses.warehouse_id"), nullable=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(140))
    region: Mapped[str] = mapped_column(String(60), default="CN-East")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DocumentFlow(TenantScopedMixin, Base):
    __tablename__ = "erp_document_flows"

    document_flow_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    case_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    source_doctype: Mapped[str] = mapped_column(String(80), index=True)
    source_id: Mapped[str] = mapped_column(String(120), index=True)
    target_doctype: Mapped[str] = mapped_column(String(80), index=True)
    target_id: Mapped[str] = mapped_column(String(120), index=True)
    relation_type: Mapped[str] = mapped_column(String(80), default="creates")
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FiscalPeriod(TenantScopedMixin, Base):
    __tablename__ = "erp_fiscal_periods"

    fiscal_period_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    company_code_id: Mapped[str] = mapped_column(ForeignKey("erp_company_codes.company_code_id"), index=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, index=True)
    period: Mapped[int] = mapped_column(Integer, index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[ErpFiscalPeriodStatus] = mapped_column(
        SqlEnum(ErpFiscalPeriodStatus),
        default=ErpFiscalPeriodStatus.OPEN,
    )


class TaxCode(TenantScopedMixin, Base):
    __tablename__ = "erp_tax_codes"

    tax_code_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    company_code_id: Mapped[str] = mapped_column(ForeignKey("erp_company_codes.company_code_id"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    description: Mapped[str] = mapped_column(String(200))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(9, 6), default=Decimal("0"))
    category: Mapped[str] = mapped_column(String(50), default="output_vat")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OpenItem(TenantScopedMixin, Base):
    __tablename__ = "erp_open_items"

    open_item_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    company_code_id: Mapped[str] = mapped_column(ForeignKey("erp_company_codes.company_code_id"), index=True)
    business_partner_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("erp_business_partners.partner_id"),
        nullable=True,
        index=True,
    )
    source_document: Mapped[str] = mapped_column(String(120), index=True)
    account_code: Mapped[str] = mapped_column(String(80))
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cleared: Mapped[bool] = mapped_column(Boolean, default=False)
    clearing_document_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChangeDocument(TenantScopedMixin, Base):
    __tablename__ = "erp_change_documents"

    change_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    change_type: Mapped[str] = mapped_column(String(60), default="UPDATE")
    changed_by: Mapped[str] = mapped_column(String(80), default="system")
    before_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Supplier(TenantScopedMixin, Base):
    __tablename__ = "erp_suppliers"

    supplier_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("erp_business_partners.partner_id"), index=True)
    supplier_code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(80), default="goods")
    rating: Mapped[str] = mapped_column(String(20), default="A")
    lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    preferred: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PurchaseOrder(TenantScopedMixin, Base):
    __tablename__ = "erp_purchase_orders"

    purchase_order_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    company_code_id: Mapped[str] = mapped_column(ForeignKey("erp_company_codes.company_code_id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("erp_suppliers.supplier_id"), index=True)
    plant_id: Mapped[str] = mapped_column(ForeignKey("erp_plants.plant_id"), index=True)
    status: Mapped[ErpProcurementStatus] = mapped_column(
        SqlEnum(ErpProcurementStatus),
        default=ErpProcurementStatus.ORDERED,
    )
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    ordered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expected_delivery_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PurchaseOrderLine(TenantScopedMixin, Base):
    __tablename__ = "erp_purchase_order_lines"

    po_line_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey("erp_purchase_orders.purchase_order_id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    tax_code_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_tax_codes.tax_code_id"), nullable=True)
    received_quantity: Mapped[int] = mapped_column(Integer, default=0)


class GoodsReceipt(TenantScopedMixin, Base):
    __tablename__ = "erp_goods_receipts"

    goods_receipt_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey("erp_purchase_orders.purchase_order_id"), index=True)
    plant_id: Mapped[str] = mapped_column(ForeignKey("erp_plants.plant_id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("erp_warehouses.warehouse_id"), index=True)
    status: Mapped[ErpGoodsReceiptStatus] = mapped_column(
        SqlEnum(ErpGoodsReceiptStatus),
        default=ErpGoodsReceiptStatus.RECEIVED,
    )
    received_by: Mapped[str] = mapped_column(String(80), default="EMP-OPS-001")
    notes: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GoodsReceiptLine(TenantScopedMixin, Base):
    __tablename__ = "erp_goods_receipt_lines"

    gr_line_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    goods_receipt_id: Mapped[str] = mapped_column(ForeignKey("erp_goods_receipts.goods_receipt_id"), index=True)
    po_line_id: Mapped[str] = mapped_column(ForeignKey("erp_purchase_order_lines.po_line_id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    batch_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    quantity_received: Mapped[int] = mapped_column(Integer)


class APInvoice(TenantScopedMixin, Base):
    __tablename__ = "erp_ap_invoices"

    ap_invoice_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey("erp_purchase_orders.purchase_order_id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("erp_suppliers.supplier_id"), index=True)
    company_code_id: Mapped[str] = mapped_column(ForeignKey("erp_company_codes.company_code_id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    status: Mapped[ErpApInvoiceStatus] = mapped_column(SqlEnum(ErpApInvoiceStatus), default=ErpApInvoiceStatus.MATCHED)
    invoice_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source_document: Mapped[str] = mapped_column(String(120), index=True)


class InventoryBatch(TenantScopedMixin, Base):
    __tablename__ = "erp_inventory_batches"

    batch_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("erp_warehouses.warehouse_id"), index=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_suppliers.supplier_id"), nullable=True)
    batch_number: Mapped[str] = mapped_column(String(80), index=True)
    manufacture_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expiry_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    quality_status: Mapped[str] = mapped_column(String(40), default="released")


class InventorySerial(TenantScopedMixin, Base):
    __tablename__ = "erp_inventory_serials"

    serial_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    batch_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_inventory_batches.batch_id"), nullable=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("erp_warehouses.warehouse_id"), index=True)
    serial_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="available")
    assigned_order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)


class StockReservation(TenantScopedMixin, Base):
    __tablename__ = "erp_stock_reservations"

    reservation_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("erp_warehouses.warehouse_id"), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    work_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[ErpReservationStatus] = mapped_column(SqlEnum(ErpReservationStatus), default=ErpReservationStatus.RESERVED)
    reserved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class DataQualityRule(Base):
    __tablename__ = "erp_data_quality_rules"

    rule_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("erp_tenant_organizations.tenant_id"), index=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    field_name: Mapped[str] = mapped_column(String(80))
    rule_type: Mapped[str] = mapped_column(String(80))
    expression: Mapped[str] = mapped_column(String(500))
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    owner_department_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CreditMemoDocument(Base):
    __tablename__ = "erp_credit_memos"

    credit_memo_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    refund_request_id: Mapped[str] = mapped_column(ForeignKey("erp_refund_requests.refund_request_id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("erp_customer_profiles.customer_id"), index=True)
    source_invoice_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_invoices.invoice_id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    reason_code: Mapped[str] = mapped_column(String(80))
    status: Mapped[ErpDocumentStatus] = mapped_column(SqlEnum(ErpDocumentStatus), default=ErpDocumentStatus.POSTED)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ClearingDocument(Base):
    __tablename__ = "erp_clearing_documents"

    clearing_document_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    company_code_id: Mapped[str] = mapped_column(ForeignKey("erp_company_codes.company_code_id"), index=True)
    business_partner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_business_partners.partner_id"), nullable=True)
    clearing_type: Mapped[str] = mapped_column(String(60), default="customer_refund")
    source_document: Mapped[str] = mapped_column(String(120), index=True)
    target_document: Mapped[str] = mapped_column(String(120), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    status: Mapped[ErpDocumentStatus] = mapped_column(SqlEnum(ErpDocumentStatus), default=ErpDocumentStatus.POSTED)
    cleared_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReversalDocument(Base):
    __tablename__ = "erp_reversal_documents"

    reversal_document_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    source_document: Mapped[str] = mapped_column(String(120), index=True)
    reason_code: Mapped[str] = mapped_column(String(80))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    status: Mapped[ErpDocumentStatus] = mapped_column(SqlEnum(ErpDocumentStatus), default=ErpDocumentStatus.POSTED)
    posted_journal_entry_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CompensationTransaction(Base):
    __tablename__ = "erp_compensation_transactions"

    compensation_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    saga_id: Mapped[str] = mapped_column(String(120), index=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(120))
    status: Mapped[ErpCompensationStatus] = mapped_column(
        SqlEnum(ErpCompensationStatus),
        default=ErpCompensationStatus.PENDING,
    )
    reason: Mapped[str] = mapped_column(String(300))
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SagaExecution(Base):
    """Durable state for a cross-system business transaction."""

    __tablename__ = "erp_saga_executions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "saga_type", "business_key", name="uq_erp_saga_business_key"),
        Index("ix_erp_saga_resume", "tenant_id", "status", "updated_at"),
    )

    saga_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    saga_type: Mapped[str] = mapped_column(String(80), index=True)
    business_key: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[ErpSagaStatus] = mapped_column(SqlEnum(ErpSagaStatus), default=ErpSagaStatus.PENDING)
    current_step: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    command_payload: Mapped[dict] = mapped_column(JSON)
    context_snapshot: Mapped[dict] = mapped_column(JSON)
    result_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SagaStep(Base):
    __tablename__ = "erp_saga_steps"
    __table_args__ = (
        UniqueConstraint("saga_id", "step_name", name="uq_erp_saga_step_name"),
        Index("ix_erp_saga_step_sequence", "saga_id", "sequence"),
    )

    step_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    saga_id: Mapped[str] = mapped_column(ForeignKey("erp_saga_executions.saga_id"), index=True)
    step_name: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[ErpSagaStepStatus] = mapped_column(
        SqlEnum(ErpSagaStepStatus), default=ErpSagaStepStatus.PENDING
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    request_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    response_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    remote_object_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ExecutionEvidence(Base):
    """Append-only evidence used to explain and audit an Agent transaction."""

    __tablename__ = "erp_execution_evidence"
    __table_args__ = (
        UniqueConstraint("saga_id", "sequence", name="uq_erp_evidence_saga_sequence"),
        Index("ix_erp_evidence_object", "tenant_id", "object_type", "object_id"),
    )

    evidence_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    saga_id: Mapped[str] = mapped_column(ForeignKey("erp_saga_executions.saga_id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    evidence_type: Mapped[str] = mapped_column(String(100), index=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    object_id: Mapped[str] = mapped_column(String(160), index=True)
    source_system: Mapped[str] = mapped_column(String(80))
    source_reference: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    previous_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    record_hash: Mapped[str] = mapped_column(String(64), index=True)
    policy_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    approval_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(120))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentEvidenceRecord(Base):
    """Append-only, task-scoped evidence shared by every Agent scenario."""

    __tablename__ = "agent_evidence_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "task_id", "evidence_id", name="uq_agent_evidence_task_id"
        ),
        UniqueConstraint(
            "tenant_id", "task_id", "sequence", name="uq_agent_evidence_task_sequence"
        ),
        Index("ix_agent_evidence_task", "tenant_id", "task_id", "sequence"),
        Index(
            "ix_agent_evidence_entity",
            "tenant_id",
            "source_system",
            "source_object",
            "entity_id",
        ),
        Index("ix_agent_evidence_trace", "tenant_id", "trace_id"),
    )

    record_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    task_id: Mapped[str] = mapped_column(String(140), index=True)
    evidence_id: Mapped[str] = mapped_column(String(140), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    scenario_id: Mapped[str] = mapped_column(String(80), index=True)
    thread_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    predicate: Mapped[str] = mapped_column(String(120), index=True)
    claim: Mapped[str] = mapped_column(String(500))
    subject: Mapped[str] = mapped_column(String(180), index=True)
    observed_value: Mapped[dict] = mapped_column(JSON)
    source_system: Mapped[str] = mapped_column(String(100), index=True)
    source_object: Mapped[str] = mapped_column(String(100), index=True)
    entity_id: Mapped[str] = mapped_column(String(180), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    valid_from: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    data_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    previous_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    record_hash: Mapped[str] = mapped_column(String(64), index=True)
    evidence_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentEvidenceRelationRecord(Base):
    """Persistent typed edge between two task-scoped evidence records."""

    __tablename__ = "agent_evidence_relations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "task_id",
            "source_record_id",
            "target_record_id",
            "relation_type",
            name="uq_agent_evidence_relation",
        ),
        Index("ix_agent_evidence_relation_task", "tenant_id", "task_id"),
    )

    relation_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    task_id: Mapped[str] = mapped_column(String(140), index=True)
    source_record_id: Mapped[str] = mapped_column(
        ForeignKey("agent_evidence_records.record_id"), index=True
    )
    target_record_id: Mapped[str] = mapped_column(
        ForeignKey("agent_evidence_records.record_id"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentDecisionRecord(Base):
    """Verifier/planner decision that must cite persisted evidence IDs."""

    __tablename__ = "agent_decision_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "task_id", "decision_id", name="uq_agent_decision_task_id"
        ),
        Index("ix_agent_decision_task", "tenant_id", "task_id", "created_at"),
    )

    record_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    task_id: Mapped[str] = mapped_column(String(140), index=True)
    decision_id: Mapped[str] = mapped_column(String(140), index=True)
    decision_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    cited_evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    decision_payload: Mapped[dict] = mapped_column(JSON)
    verifier: Mapped[str] = mapped_column(String(100), default="deterministic_verifier")
    trace_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentDelegationGrant(Base):
    """Durable record behind a short-lived Agent capability token."""

    __tablename__ = "agent_delegation_grants"
    __table_args__ = (
        Index("ix_agent_delegation_principal", "tenant_id", "principal_id", "status"),
        Index("ix_agent_delegation_agent", "tenant_id", "agent_id", "expires_at"),
        UniqueConstraint("tenant_id", "token_jti_hash", name="uq_agent_delegation_jti"),
    )

    grant_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    principal_id: Mapped[str] = mapped_column(String(120), index=True)
    principal_role: Mapped[str] = mapped_column(String(40))
    issued_by: Mapped[str] = mapped_column(String(120), index=True)
    agent_id: Mapped[str] = mapped_column(String(120), index=True)
    allowed_tools: Mapped[list] = mapped_column(JSON, default=list)
    resource_scopes: Mapped[dict] = mapped_column(JSON, default=dict)
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    purpose: Mapped[str] = mapped_column(String(500), default="")
    approval_id: Mapped[Optional[str]] = mapped_column(String(140), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    token_jti_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AgentFeedbackRecord(Base):
    """Human outcome feedback linked to a production Agent execution."""

    __tablename__ = "agent_feedback_records"
    __table_args__ = (
        Index("ix_agent_feedback_window", "tenant_id", "scenario_id", "created_at"),
        Index("ix_agent_feedback_version", "tenant_id", "created_at"),
    )

    feedback_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    thread_id: Mapped[str] = mapped_column(String(120), index=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(140), nullable=True, index=True)
    scenario_id: Mapped[str] = mapped_column(String(100), index=True)
    submitted_by: Mapped[str] = mapped_column(String(120), index=True)
    disposition: Mapped[str] = mapped_column(String(30), index=True)
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    correction: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    task_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    version_context: Mapped[dict] = mapped_column(JSON, default=dict)
    eval_candidate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class OnlineEvalCase(Base):
    """Governed candidate promoted from production feedback into an eval set."""

    __tablename__ = "online_eval_cases"
    __table_args__ = (
        Index("ix_online_eval_dataset", "tenant_id", "dataset_name", "status"),
        UniqueConstraint("tenant_id", "source_feedback_id", name="uq_online_eval_feedback"),
    )

    case_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    source_feedback_id: Mapped[str] = mapped_column(
        ForeignKey("agent_feedback_records.feedback_id"), index=True
    )
    dataset_name: Mapped[str] = mapped_column(String(120), default="production_feedback", index=True)
    scenario_id: Mapped[str] = mapped_column(String(100), index=True)
    input_snapshot: Mapped[dict] = mapped_column(JSON)
    expected_outcome: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="candidate", index=True)
    dataset_version: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class CounterfactualExperiment(Base):
    """Side-effect-free replay comparing model, prompt, policy and plan variants."""

    __tablename__ = "counterfactual_experiments"
    __table_args__ = (
        Index("ix_counterfactual_task", "tenant_id", "thread_id", "created_at"),
    )

    experiment_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    thread_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(140), nullable=True, index=True)
    requested_by: Mapped[str] = mapped_column(String(120), index=True)
    baseline_snapshot: Mapped[dict] = mapped_column(JSON)
    variants: Mapped[list] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="completed", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class A2ATask(Base):
    """Durable Agent2Agent task used by Joule BYOA and other A2A clients."""

    __tablename__ = "a2a_tasks"
    __table_args__ = (
        Index("ix_a2a_task_claim", "status", "next_attempt_at", "created_at"),
        Index("ix_a2a_task_context", "tenant_id", "context_id", "updated_at"),
    )

    task_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    context_id: Mapped[str] = mapped_column(String(120), index=True)
    owner_user_id: Mapped[str] = mapped_column(String(120), index=True)
    scenario_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="submitted", index=True)
    input_message: Mapped[dict] = mapped_column(JSON)
    output_artifacts: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    task_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    push_notifications: Mapped[bool] = mapped_column(Boolean, default=False)
    callback_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    callback_auth_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    callback_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    callback_attempts: Mapped[int] = mapped_column(Integer, default=0)
    callback_next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    callback_last_error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class A2ATaskEvent(Base):
    __tablename__ = "a2a_task_events"
    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="uq_a2a_task_event_sequence"),
        Index("ix_a2a_task_event_order", "tenant_id", "task_id", "sequence"),
    )

    event_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("a2a_tasks.task_id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    event_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TenantOnboarding(Base):
    __tablename__ = "tenant_onboarding"

    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("erp_tenant_organizations.tenant_id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(40), default="DRAFT", index=True)
    environment: Mapped[str] = mapped_column(String(40), default="sandbox")
    primary_connector_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    checklist: Mapped[dict] = mapped_column(JSON)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TenantSubscription(Base):
    __tablename__ = "tenant_subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_subscription_tenant"),
    )

    subscription_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("erp_tenant_organizations.tenant_id"), index=True
    )
    plan_code: Mapped[str] = mapped_column(String(50), default="PILOT")
    status: Mapped[str] = mapped_column(String(40), default="TRIAL", index=True)
    monthly_action_quota: Mapped[int] = mapped_column(Integer, default=1000)
    billing_currency: Mapped[str] = mapped_column(String(3), default="CNY")
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    trial_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "metric_name", "source_type", "source_id", name="uq_usage_source"
        ),
        Index("ix_usage_period", "tenant_id", "occurred_at", "metric_name"),
    )

    usage_event_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    metric_name: Mapped[str] = mapped_column(String(80), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("1.0000"))
    unit: Mapped[str] = mapped_column(String(40), default="action")
    source_type: Mapped[str] = mapped_column(String(80), index=True)
    source_id: Mapped[str] = mapped_column(String(160), index=True)
    usage_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class TenantSLO(Base):
    __tablename__ = "tenant_slos"
    __table_args__ = (
        UniqueConstraint("tenant_id", "metric_name", name="uq_tenant_slo_metric"),
    )

    slo_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    metric_name: Mapped[str] = mapped_column(String(80), index=True)
    target_operator: Mapped[str] = mapped_column(String(10), default=">=")
    target_value: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    unit: Mapped[str] = mapped_column(String(30))
    window_minutes: Mapped[int] = mapped_column(Integer, default=43200)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProcurementApprovalRequest(TenantScopedMixin, Base):
    __tablename__ = "erp_procurement_approval_requests"

    procurement_request_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey("erp_purchase_orders.purchase_order_id"), index=True)
    requester_employee_id: Mapped[str] = mapped_column(String(50), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("erp_suppliers.supplier_id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[ErpBusinessRequestStatus] = mapped_column(
        SqlEnum(ErpBusinessRequestStatus),
        default=ErpBusinessRequestStatus.PENDING_APPROVAL,
    )
    approval_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    policy_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReimbursementClaim(TenantScopedMixin, Base):
    __tablename__ = "erp_reimbursement_claims"

    reimbursement_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    requester_employee_id: Mapped[str] = mapped_column(String(50), index=True)
    cost_center_id: Mapped[str] = mapped_column(ForeignKey("erp_cost_centers.cost_center_id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    category: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(500))
    receipt_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[ErpBusinessRequestStatus] = mapped_column(
        SqlEnum(ErpBusinessRequestStatus),
        default=ErpBusinessRequestStatus.SUBMITTED,
    )
    approval_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    policy_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AccessRequestRecord(TenantScopedMixin, Base):
    __tablename__ = "erp_access_requests"

    access_request_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    requester_employee_id: Mapped[str] = mapped_column(String(50), index=True)
    target_system: Mapped[str] = mapped_column(String(120), index=True)
    permission_level: Mapped[str] = mapped_column(String(80))
    business_reason: Mapped[str] = mapped_column(String(500))
    risk_level: Mapped[str] = mapped_column(String(20), default="high")
    status: Mapped[ErpBusinessRequestStatus] = mapped_column(
        SqlEnum(ErpBusinessRequestStatus),
        default=ErpBusinessRequestStatus.PENDING_APPROVAL,
    )
    approval_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    granted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    policy_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MasterDataVersion(TenantScopedMixin, Base):
    __tablename__ = "erp_master_data_versions"

    version_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    data_snapshot: Mapped[dict] = mapped_column(JSON)
    changed_by: Mapped[str] = mapped_column(String(80), default="system")
    change_reason: Mapped[str] = mapped_column(String(300), default="initial_version")
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MasterDataChangeRequest(TenantScopedMixin, Base):
    __tablename__ = "erp_master_data_change_requests"

    mdg_request_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    change_type: Mapped[str] = mapped_column(String(80), default="UPDATE")
    proposed_change: Mapped[dict] = mapped_column(JSON)
    validation_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[ErpMasterDataRequestStatus] = mapped_column(
        SqlEnum(ErpMasterDataRequestStatus),
        default=ErpMasterDataRequestStatus.PENDING_APPROVAL,
    )
    requested_by: Mapped[str] = mapped_column(String(80), default="system")
    reviewer_employee_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MasterDataValidationResult(TenantScopedMixin, Base):
    __tablename__ = "erp_master_data_validation_results"

    validation_result_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    mdg_request_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("erp_master_data_change_requests.mdg_request_id"),
        nullable=True,
        index=True,
    )
    rule_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_data_quality_rules.rule_id"), nullable=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    field_name: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[str] = mapped_column(String(500))
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MasterDataDuplicateCandidate(TenantScopedMixin, Base):
    __tablename__ = "erp_master_data_duplicate_candidates"

    duplicate_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    left_object_id: Mapped[str] = mapped_column(String(120), index=True)
    right_object_id: Mapped[str] = mapped_column(String(120), index=True)
    match_score: Mapped[float] = mapped_column(Float)
    matched_fields: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="open")
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExternalSystemConnector(Base):
    __tablename__ = "erp_external_system_connectors"

    connector_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    name: Mapped[str] = mapped_column(String(160))
    system_type: Mapped[str] = mapped_column(String(80), index=True)
    base_url: Mapped[str] = mapped_column(String(300))
    auth_type: Mapped[str] = mapped_column(String(60), default="api_key")
    status: Mapped[ErpConnectorStatus] = mapped_column(SqlEnum(ErpConnectorStatus), default=ErpConnectorStatus.ACTIVE)
    capabilities: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    last_health_check_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WebhookSubscription(Base):
    __tablename__ = "erp_webhook_subscriptions"

    subscription_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    connector_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("erp_external_system_connectors.connector_id"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    target_url: Mapped[str] = mapped_column(String(300))
    secret_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OutboxEvent(Base):
    __tablename__ = "erp_outbox_events"
    __table_args__ = (
        Index("ix_erp_outbox_claim", "status", "next_attempt_at", "created_at"),
    )

    outbox_event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(120), index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[ErpOutboxStatus] = mapped_column(SqlEnum(ErpOutboxStatus), default=ErpOutboxStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    dead_lettered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    replay_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class IdempotencyRecord(Base):
    __tablename__ = "erp_idempotency_records"

    idempotency_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    scope: Mapped[str] = mapped_column(String(80), index=True)
    request_hash: Mapped[str] = mapped_column(String(128))
    response_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[ErpIdempotencyStatus] = mapped_column(
        SqlEnum(ErpIdempotencyStatus),
        default=ErpIdempotencyStatus.IN_PROGRESS,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SystemReconciliationIssue(Base):
    __tablename__ = "erp_system_reconciliation_issues"

    reconciliation_issue_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    source_system: Mapped[str] = mapped_column(String(80))
    target_system: Mapped[str] = mapped_column(String(80))
    mismatch_type: Mapped[str] = mapped_column(String(100))
    source_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    target_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="high")
    status: Mapped[str] = mapped_column(String(40), default="open")
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PIIRecord(Base):
    """Encrypted PII separated from operational business tables."""

    __tablename__ = "erp_pii_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "subject_type", "subject_id", "field_name", name="uq_erp_pii_subject_field"
        ),
        Index("ix_erp_pii_retention", "expires_at", "deleted_at"),
    )

    pii_record_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    subject_type: Mapped[str] = mapped_column(String(80), index=True)
    subject_id: Mapped[str] = mapped_column(String(120), index=True)
    field_name: Mapped[str] = mapped_column(String(80))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_data_key: Mapped[bytes] = mapped_column(LargeBinary)
    key_id: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(120))
    legal_basis: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChangeDataCaptureEvent(Base):
    __tablename__ = "erp_cdc_events"
    __table_args__ = (
        UniqueConstraint("source_system", "source_position", name="uq_erp_cdc_source_position"),
        Index("ix_erp_cdc_unpublished", "published_at", "occurred_at"),
    )

    cdc_event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    source_system: Mapped[str] = mapped_column(String(80), index=True)
    source_position: Mapped[str] = mapped_column(String(200))
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    operation: Mapped[str] = mapped_column(String(20))
    before_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class CDCCheckpoint(Base):
    __tablename__ = "erp_cdc_checkpoints"

    checkpoint_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    source_system: Mapped[str] = mapped_column(String(80), index=True)
    stream_name: Mapped[str] = mapped_column(String(120))
    source_position: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DataContract(Base):
    __tablename__ = "erp_data_contracts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "contract_name", name="uq_erp_data_contract_name"),
    )

    contract_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    contract_name: Mapped[str] = mapped_column(String(160), index=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    owner: Mapped[str] = mapped_column(String(120))
    compatibility_mode: Mapped[str] = mapped_column(String(30), default="BACKWARD")
    active_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DataContractVersion(TenantScopedMixin, Base):
    __tablename__ = "erp_data_contract_versions"
    __table_args__ = (
        UniqueConstraint("contract_id", "version", name="uq_erp_data_contract_version"),
    )

    contract_version_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("erp_data_contracts.contract_id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    schema_definition: Mapped[dict] = mapped_column(JSON)
    schema_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")
    created_by: Mapped[str] = mapped_column(String(120), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DataLineageEdge(Base):
    __tablename__ = "erp_data_lineage_edges"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_asset",
            "target_asset",
            "transformation",
            name="uq_erp_lineage_edge",
        ),
    )

    lineage_edge_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    source_asset: Mapped[str] = mapped_column(String(200), index=True)
    target_asset: Mapped[str] = mapped_column(String(200), index=True)
    transformation: Mapped[str] = mapped_column(String(200))
    job_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    column_mapping: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EventArchive(Base):
    """High-volume append-only events; range-partitioned on PostgreSQL."""

    __tablename__ = "erp_event_archive"
    __table_args__ = (
        Index("ix_erp_event_archive_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_erp_event_archive_type_occurred", "event_type", "occurred_at"),
    )

    archive_event_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    source_table: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(140), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class BillOfMaterial(TenantScopedMixin, Base):
    __tablename__ = "erp_bills_of_material"

    bom_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    version: Mapped[str] = mapped_column(String(40), default="1")
    status: Mapped[ErpBomStatus] = mapped_column(SqlEnum(ErpBomStatus), default=ErpBomStatus.ACTIVE)
    output_quantity: Mapped[int] = mapped_column(Integer, default=1)
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class BillOfMaterialLine(TenantScopedMixin, Base):
    __tablename__ = "erp_bill_of_material_lines"

    bom_line_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    bom_id: Mapped[str] = mapped_column(ForeignKey("erp_bills_of_material.bom_id"), index=True)
    component_product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    scrap_rate: Mapped[float] = mapped_column(Float, default=0.0)


class WorkOrder(TenantScopedMixin, Base):
    __tablename__ = "erp_work_orders"

    work_order_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    bom_id: Mapped[str] = mapped_column(ForeignKey("erp_bills_of_material.bom_id"), index=True)
    plant_id: Mapped[str] = mapped_column(ForeignKey("erp_plants.plant_id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    status: Mapped[ErpWorkOrderStatus] = mapped_column(SqlEnum(ErpWorkOrderStatus), default=ErpWorkOrderStatus.PLANNED)
    planned_quantity: Mapped[int] = mapped_column(Integer, default=0)
    completed_quantity: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_start_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scheduled_end_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    actual_start_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    actual_end_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class WorkOrderComponentIssue(TenantScopedMixin, Base):
    __tablename__ = "erp_work_order_component_issues"

    issue_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("erp_work_orders.work_order_id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("erp_products.product_id"), index=True)
    batch_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_inventory_batches.batch_id"), nullable=True)
    quantity: Mapped[float] = mapped_column(Float)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FixedAsset(TenantScopedMixin, Base):
    __tablename__ = "erp_fixed_assets"

    asset_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    company_code_id: Mapped[str] = mapped_column(ForeignKey("erp_company_codes.company_code_id"), index=True)
    plant_id: Mapped[Optional[str]] = mapped_column(ForeignKey("erp_plants.plant_id"), nullable=True)
    asset_tag: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(80))
    acquisition_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    status: Mapped[ErpAssetStatus] = mapped_column(SqlEnum(ErpAssetStatus), default=ErpAssetStatus.IN_SERVICE)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    useful_life_months: Mapped[int] = mapped_column(Integer, default=36)


class AssetDepreciationRun(TenantScopedMixin, Base):
    __tablename__ = "erp_asset_depreciation_runs"

    depreciation_run_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("erp_fixed_assets.asset_id"), index=True)
    fiscal_period_id: Mapped[str] = mapped_column(ForeignKey("erp_fiscal_periods.fiscal_period_id"), index=True)
    depreciation_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    accumulated_depreciation: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    posted_journal_entry_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CurrencyRate(TenantScopedMixin, Base):
    __tablename__ = "erp_currency_rates"

    currency_rate_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    from_currency: Mapped[str] = mapped_column(String(10), index=True)
    to_currency: Mapped[str] = mapped_column(String(10), index=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    provider: Mapped[str] = mapped_column(String(80), default="demo_fx")
    valid_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PeriodCloseRun(TenantScopedMixin, Base):
    __tablename__ = "erp_period_close_runs"

    close_run_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    company_code_id: Mapped[str] = mapped_column(ForeignKey("erp_company_codes.company_code_id"), index=True)
    fiscal_period_id: Mapped[str] = mapped_column(ForeignKey("erp_fiscal_periods.fiscal_period_id"), index=True)
    status: Mapped[ErpConsolidationStatus] = mapped_column(
        SqlEnum(ErpConsolidationStatus),
        default=ErpConsolidationStatus.VALIDATED,
    )
    revenue_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    expense_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    open_item_count: Mapped[int] = mapped_column(Integer, default=0)
    closed_by: Mapped[str] = mapped_column(String(80), default="EMP-FIN-001")
    notes: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConsolidationGroup(Base):
    __tablename__ = "erp_consolidation_groups"

    group_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("erp_tenant_organizations.tenant_id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    reporting_currency: Mapped[str] = mapped_column(String(10), default="CNY")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ConsolidationRun(TenantScopedMixin, Base):
    __tablename__ = "erp_consolidation_runs"

    consolidation_run_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("erp_consolidation_groups.group_id"), index=True)
    fiscal_period_id: Mapped[str] = mapped_column(ForeignKey("erp_fiscal_periods.fiscal_period_id"), index=True)
    status: Mapped[ErpConsolidationStatus] = mapped_column(
        SqlEnum(ErpConsolidationStatus),
        default=ErpConsolidationStatus.VALIDATED,
    )
    total_revenue: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    total_expense: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    elimination_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    reporting_currency: Mapped[str] = mapped_column(String(10), default="CNY")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BusinessApprovalRecord(Base):
    __tablename__ = "erp_business_approvals"

    approval_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    scenario: Mapped[str] = mapped_column(String(80), index=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    object_id: Mapped[str] = mapped_column(String(80), index=True)
    refund_request_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("erp_refund_requests.refund_request_id"),
        nullable=True,
        index=True,
    )
    stage_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[ErpDocumentStatus] = mapped_column(SqlEnum(ErpDocumentStatus), default=ErpDocumentStatus.SUBMITTED)
    required_role: Mapped[str] = mapped_column(String(50))
    approver_employee_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    approval_limit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    decision_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    refund_request: Mapped[Optional["RefundRequest"]] = relationship(back_populates="approvals")


class FinancialLedgerEntry(Base):
    __tablename__ = "erp_financial_ledger_entries"

    ledger_entry_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    refund_request_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("erp_refund_requests.refund_request_id"),
        nullable=True,
        index=True,
    )
    entry_type: Mapped[ErpLedgerEntryType] = mapped_column(SqlEnum(ErpLedgerEntryType))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    debit_account: Mapped[str] = mapped_column(String(80))
    credit_account: Mapped[str] = mapped_column(String(80))
    status: Mapped[ErpDocumentStatus] = mapped_column(SqlEnum(ErpDocumentStatus), default=ErpDocumentStatus.POSTED)
    source_document: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    order: Mapped[Optional["Order"]] = relationship()
    refund_request: Mapped[Optional["RefundRequest"]] = relationship(back_populates="ledger_entries")


class ProcessEventLog(Base):
    __tablename__ = "erp_process_event_logs"

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    case_id: Mapped[str] = mapped_column(String(100), index=True)
    object_type: Mapped[str] = mapped_column(String(80), index=True)
    object_id: Mapped[str] = mapped_column(String(100), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(80), default="system")
    actor_role: Mapped[str] = mapped_column(String(50), default="SYSTEM")
    before_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    event_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
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
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    refund_id: Mapped[str] = mapped_column(String(100), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    ticket: Mapped["Ticket"] = relationship(back_populates="refund_log")

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    thread_id: Mapped[str] = mapped_column(String(100), index=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    node_name: Mapped[str] = mapped_column(String(50))
    event_type: Mapped[str] = mapped_column(String(50))
    input_data: Mapped[Optional[dict]] = mapped_column(JSON)
    output_data: Mapped[Optional[dict]] = mapped_column(JSON)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    success: Mapped[Optional[bool]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    request_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    scenario_id: Mapped[str] = mapped_column(String(80), index=True)
    approval_type: Mapped[str] = mapped_column(String(80), index=True)
    action: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    reviewer_id: Mapped[str] = mapped_column(String(100))
    reviewer_role: Mapped[str] = mapped_column(String(50))
    review_roles: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    thread_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    policy_event: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LLMUsageRecord(Base):
    """Provider-neutral token and estimated-cost ledger for every LLM call."""

    __tablename__ = "llm_usage_records"
    __table_args__ = (
        Index("ix_llm_usage_tenant_created", "tenant_id", "created_at"),
        Index("ix_llm_usage_thread_created", "thread_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    thread_id: Mapped[str] = mapped_column(String(120), index=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    node_name: Mapped[str] = mapped_column(String(80), index=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    model: Mapped[str] = mapped_column(String(120), index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    input_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    output_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    fallback_index: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    prompt_variant: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    prompt_rollout_bucket: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApprovalTask(Base):
    """Durable approval inbox item independent from a completed decision."""

    __tablename__ = "approval_tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "task_key", name="uq_approval_task_tenant_key"),
        Index("ix_approval_tasks_queue", "tenant_id", "status", "due_at"),
        Index("ix_approval_tasks_requester", "tenant_id", "requester_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    task_key: Mapped[str] = mapped_column(String(180), index=True)
    request_id: Mapped[str] = mapped_column(String(120), index=True)
    scenario_id: Mapped[str] = mapped_column(String(80), index=True)
    approval_type: Mapped[str] = mapped_column(String(80), index=True)
    stage_id: Mapped[str] = mapped_column(String(100), default="human_review")
    stage_name: Mapped[str] = mapped_column(String(160), default="Human review")
    requester_id: Mapped[str] = mapped_column(String(120), index=True)
    requester_role: Mapped[str] = mapped_column(String(50), default="USER")
    assigned_roles: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    business_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    thread_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    current_assignee_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    escalation_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    reviewer_comment: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    escalated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class UserLongTermMemory(Base):
    """Structured, governed cross-session memory used by specialist agents."""

    __tablename__ = "user_long_term_memories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "memory_type", "memory_key", name="uq_user_ltm_key"),
        Index("ix_user_ltm_lookup", "tenant_id", "user_id", "status", "importance"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    memory_type: Mapped[str] = mapped_column(String(50), index=True)
    memory_key: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(String(1000))
    attributes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    importance: Mapped[int] = mapped_column(Integer, default=50)
    source_thread_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(50), default="workflow")
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentExecutionJob(Base):
    """Durable horizontally claimable queue for non-streaming Agent execution."""

    __tablename__ = "agent_execution_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_agent_job_idempotency"),
        Index("ix_agent_jobs_claim", "status", "available_at", "priority"),
        Index("ix_agent_jobs_tenant_created", "tenant_id", "created_at"),
    )

    job_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180))
    thread_id: Mapped[str] = mapped_column(String(120), index=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    requester_id: Mapped[str] = mapped_column(String(120), index=True)
    requester_role: Mapped[str] = mapped_column(String(50), default="USER")
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=50)
    input_payload: Mapped[dict] = mapped_column(JSON)
    output_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserMemory(Base):
    """
    用户跨会话记忆表 — 持久化风控相关的用户画像

    - 每个用户一条记录（unique on user_id）
    - 每次退款完成或审批拒绝后由相应节点 upsert
    - risk_check_node 读取此表来补充实时风控评分
    """
    __tablename__ = "user_memory"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_user_memory_tenant_user"),
        Index("ix_user_memory_tenant_user", "tenant_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
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


class KnowledgeDocument(Base):
    """Versioned, permission-aware source document for policy RAG."""

    __tablename__ = "knowledge_documents"

    document_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    title: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(50), default="1")
    permission_roles: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chunks: Mapped[List["KnowledgeChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class KnowledgeChunk(Base):
    """Paragraph-level chunk with a pgvector embedding and stable citation ID."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "paragraph_id", name="uq_knowledge_chunk_paragraph"),
        Index("ix_knowledge_chunks_tenant_document", "tenant_id", "document_id"),
    )

    chunk_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default=current_tenant_id, index=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.document_id", ondelete="CASCADE"), index=True
    )
    paragraph_id: Mapped[str] = mapped_column(String(100), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    embedding: Mapped[Optional[list[float]]] = mapped_column(
        Vector(768).with_variant(JSON(), "sqlite"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    document: Mapped["KnowledgeDocument"] = relationship(back_populates="chunks")
