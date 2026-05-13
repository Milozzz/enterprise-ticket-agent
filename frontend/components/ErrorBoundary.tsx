"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertCircle, RotateCcw } from "lucide-react";

type Props = {
  children: ReactNode;
  /** 覆盖默认的企业级降级 UI */
  fallback?: ReactNode;
  /**
   * 用户点击「尝试恢复」且边界已重置后调用（例如通知父组件刷新数据）。
   * 与 `resetErrorBoundary` 二选一即可；若同时传入，二者都会在重试时调用。
   */
  onRetry?: () => void;
  /**
   * 与 react-error-boundary 命名对齐：等价于在重置后额外执行的回调。
   * 实际重置由本组件内部完成，无需由父组件传入实现。
   */
  resetErrorBoundary?: () => void;
};

type State = {
  hasError: boolean;
  error: Error | null;
};

function errorTraceSnippet(error: Error | null): string {
  const msg = error?.message?.trim();
  if (!msg) return "ERR_UI_CRASH";
  const s = msg.slice(0, 20);
  return s.length < msg.length ? `${s}…` : s;
}

/**
 * Generative UI 专用：子组件渲染抛错时不拖垮整页，展示企业级降级面板并支持重试。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary] Generative UI render failed:", error.message, info.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
    this.props.onRetry?.();
    this.props.resetErrorBoundary?.();
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback != null) return this.props.fallback;

      const trace = errorTraceSnippet(this.state.error);

      return (
        <div
          role="alert"
          className="flex w-full flex-col gap-3 rounded-xl border border-rose-100 bg-rose-50/50 p-4"
        >
          <div className="flex items-start gap-3">
            <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-rose-500" aria-hidden />
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <p className="text-sm font-semibold text-rose-900">组件渲染失败 (Render Error)</p>
              <p className="text-xs leading-relaxed text-slate-600">
                该动态卡片暂时无法显示。核心对话引擎仍在正常运行，您可以继续输入文字指令或重试加载。
              </p>
            </div>
          </div>

          <div className="mt-1 flex items-center justify-between border-t border-rose-100/50 pt-3">
            <span
              className="rounded bg-rose-100/50 px-1.5 py-0.5 font-mono text-[10px] text-rose-400"
              title={this.state.error?.message ?? "ERR_UI_CRASH"}
            >
              {trace}
            </span>
            <button
              type="button"
              onClick={this.handleRetry}
              className="flex items-center rounded-lg border border-rose-200 bg-white px-2.5 py-1.5 text-xs font-medium text-rose-700 shadow-sm transition-colors hover:bg-rose-50 hover:text-rose-800"
            >
              <RotateCcw className="mr-1.5 h-3 w-3" aria-hidden />
              尝试恢复 (Retry)
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
