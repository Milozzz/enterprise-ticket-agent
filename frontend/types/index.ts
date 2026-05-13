// ============================================================
// 工单相关类型
// ============================================================

export type TicketStatus =
  | "pending"
  | "processing"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "completed"
  | "escalated";

export type RefundReason =
  | "damaged"
  | "wrong_item"
  | "not_received"
  | "quality_issue"
  | "other";

export interface Ticket {
  id: string;
  orderId: string;
  userId: string;
  reason: RefundReason;
  description: string;
  status: TicketStatus;
  amount: number;
  riskScore: number;
  createdAt: string;
  updatedAt: string;
  threadId: string;
}

// ============================================================
// 订单相关类型
// ============================================================

export type OrderStatus =
  | "pending"
  | "paid"
  | "shipped"
  | "delivered"
  | "refunded"
  | "cancelled";

export interface OrderItem {
  id: string;
  name: string;
  imageUrl: string;
  quantity: number;
  price: number;
}

export interface Order {
  id: string;
  userId: string;
  status: OrderStatus;
  items: OrderItem[];
  totalAmount: number;
  shippingAddress: string;
  createdAt: string;
  trackingNumber?: string;
  carrier?: string;
}

// ============================================================
// Generative UI — Agent 返回的 UI 组件描述
// ============================================================

export type UIComponentType =
  | "order_card"
  | "approval_panel"
  | "refund_timeline"
  | "risk_alert"
  | "email_preview"
  | "thinking_stream";

export interface UIComponent {
  type: UIComponentType;
  data: Record<string, unknown>;
}

// ============================================================
// Agent 状态相关类型
// ============================================================

export type AgentStep =
  | "classifying"
  | "looking_up_order"
  | "checking_risk"
  | "executing_refund"
  | "sending_notification"
  | "awaiting_human"
  | "completed"
  | "error";

export interface AgentThinkingStep {
  step: AgentStep;
  label: string;
  status: "pending" | "running" | "done" | "error";
  detail?: string;
  timestamp?: string;
}

// ============================================================
// Chat 消息类型（扩展 Vercel AI SDK）
// ============================================================

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  uiComponents?: UIComponent[];
  agentSteps?: AgentThinkingStep[];
  createdAt?: Date;
}

// ============================================================
// Dashboard 统计类型
// ============================================================

export interface DashboardStats {
  totalTickets: number;
  autoResolvedRate: number;
  avgProcessingTimeMinutes: number;
  riskInterceptedCount: number;
  costSavedAmount: number;
  ticketsByStatus: Record<TicketStatus, number>;
  dailyTrend: Array<{ date: string; count: number; autoResolved: number }>;
  /** 后端扩展字段（可选） */
  auditEvents24h?: number;
  source?: string;
  error?: string;
}

export interface NodeLatencyStat {
  node: string;
  count: number;
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
  max_ms: number;
  failure_count?: number;
  failure_rate?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}
