import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatCurrency(amount: number) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
  }).format(amount)
}

export function formatDate(dateString: string) {
  if (!dateString) return "N/A"
  return new Date(dateString).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function getStatusColor(status: string) {
  const colors: Record<string, string> = {
    pending: "bg-yellow-100 text-yellow-800 border-yellow-200",
    processing: "bg-blue-100 text-blue-800 border-blue-200",
    completed: "bg-green-100 text-green-800 border-green-200",
    delivered: "bg-green-100 text-green-800 border-green-200",
    shipped: "bg-purple-100 text-purple-800 border-purple-200",
    cancelled: "bg-red-100 text-red-800 border-red-200",
    rejected: "bg-red-100 text-red-800 border-red-200",
  }
  return colors[status.toLowerCase()] || "bg-gray-100 text-gray-800 border-gray-200"
}

export function getStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待处理",
    processing: "处理中",
    completed: "已完成",
    delivered: "已送达",
    shipped: "已发货",
    cancelled: "已取消",
    rejected: "已拒绝",
  }
  return labels[status.toLowerCase()] || status
}

export function getRiskLevel(score: number) {
  if (score >= 70) return { label: "极高风险", color: "text-red-600", bg: "bg-red-50" }
  if (score >= 40) return { label: "中等风险", color: "text-orange-600", bg: "bg-orange-50" }
  return { label: "低风险", color: "text-green-600", bg: "bg-green-50" }
}
