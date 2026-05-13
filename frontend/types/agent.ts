/**
 * 前后端共享类型定义
 * 严格对齐 backend/app/models/ticket.py 中的 Pydantic Schema
 */

// ── 订单相关 ──────────────────────────────────────────────
export interface OrderItem {
  id: string;
  name: string;
  image_url?: string;
  imageUrl?: string;
  quantity: number;
  price: number;
}

export interface OrderDetail {
  id: string;
  user_id: string;
  status: string;
  items: OrderItem[];
  total_amount?: number;
  totalAmount?: number;
  shipping_address?: string;
  shippingAddress?: string;
  created_at?: string;
  createdAt?: string;
  tracking_number?: string | null;
  trackingNumber?: string | null;
  carrier?: string | null;
}

// ── 风控相关 ──────────────────────────────────────────────
export type RiskLevel = "low" | "medium" | "high";

export interface RiskCheckResult {
  // 工具返回 camelCase，同时兼容 Pydantic snake_case
  riskScore?: number;
  risk_score?: number;
  riskLevel?: RiskLevel;
  risk_level?: RiskLevel;
  reasons: string[];
  autoApprove?: boolean;
  auto_approve?: boolean;
  threshold: number;
  recommendation?: string;
}

// ── 审批面板 ──────────────────────────────────────────────
export interface ApprovalPanelProps {
  ticketId: string;
  threadId: string;
  orderAmount: number;
  riskScore: number;
}

// ── 退款进度时间线 ────────────────────────────────────────
export interface TimelineStep {
  label: string;
  description?: string;
  timestamp?: string;
  status: "completed" | "current" | "pending";
}

export interface RefundTimelineData {
  steps: TimelineStep[];
}

// ── 邮件预览 ──────────────────────────────────────────────
export interface EmailPreviewData {
  to: string;
  subject: string;
  body: string;
  sentAt?: string;
  status?: "preview" | "sent";
}

// ── Agent 思考流 ──────────────────────────────────────────
export interface ThinkingStep {
  step: string;
  label: string;
  status: "running" | "done" | "error";
  detail?: string;
}

export interface AgentThinkingStreamProps {
  steps: ThinkingStep[];
}

// ── SSE 通信协议 ──────────────────────────────────────────
export type UiEventType =
  | "AgentThinkingStream"
  | "OrderCard"
  | "RiskAlert"
  | "ApprovalPanel"
  | "RefundTimeline"
  | "EmailPreview";

export interface UiEvent {
  type: UiEventType | string;
  props: unknown;
}

// ── 审计日志 ──────────────────────────────────────────────
export interface AuditLogEntry {
  node: string;
  event: string;
  input: Record<string, any>;
  output: Record<string, any>;
  time: string;
}

// ── Chat 消息 ─────────────────────────────────────────────
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  /** 一条消息可以携带多个 Generative UI 组件 */
  uiEvents?: UiEvent[];
}
