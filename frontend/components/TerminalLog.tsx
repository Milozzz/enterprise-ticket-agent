"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

export type TerminalLogLevel = "info" | "warn" | "error";

export interface TerminalLogLine {
  id: string;
  text: string;
  level?: TerminalLogLevel;
}

export interface TerminalLogProps {
  lines: TerminalLogLine[];
  /** 仅最后一行逐字打出；上一行在出现新行时自动瞬间补全 */
  typewriter?: boolean;
  /** 每个字符间隔（毫秒） */
  charDelayMs?: number;
  className?: string;
}

const LEVEL_CLASS: Record<TerminalLogLevel, string> = {
  info: "text-emerald-400",
  warn: "text-amber-400",
  error: "text-red-400",
};

function TypewriterRow({
  text,
  level,
  animate,
  charDelayMs,
}: {
  text: string;
  level: TerminalLogLevel;
  animate: boolean;
  charDelayMs: number;
}) {
  const [visibleLen, setVisibleLen] = useState(animate ? 0 : text.length);

  useEffect(() => {
    if (!animate) {
      setVisibleLen(text.length);
      return;
    }

    setVisibleLen(0);
    let i = 0;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const step = () => {
      if (cancelled) return;
      i += 1;
      setVisibleLen(Math.min(i, text.length));
      if (i < text.length) {
        timer = setTimeout(step, charDelayMs);
      }
    };

    timer = setTimeout(step, charDelayMs);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [text, animate, charDelayMs]);

  const visible = text.slice(0, visibleLen);
  const showCaret = animate && visibleLen < text.length;

  return (
    <div
      className={cn(
        "font-mono text-[11px] leading-relaxed tracking-wide break-all",
        LEVEL_CLASS[level]
      )}
    >
      <span>{visible}</span>
      {showCaret ? (
        <span
          className="ml-px inline-block min-h-[1em] w-2 translate-y-0.5 bg-current opacity-90 animate-pulse align-middle"
          aria-hidden
        />
      ) : null}
    </div>
  );
}

/**
 * 终端风格日志：绿色等宽字体 + 逐字打字机（可选）；info / warn / error 三色。
 */
export function TerminalLog({
  lines,
  typewriter = true,
  charDelayMs = 14,
  className,
}: TerminalLogProps) {
  if (lines.length === 0) return null;

  return (
    <div
      className={cn(
        "rounded-lg border border-slate-700/80 bg-slate-950 px-3 py-2.5 shadow-inner",
        className
      )}
      role="log"
      aria-label="终端日志"
    >
      <div className="space-y-1.5">
        {lines.map((line, index) => {
          const level: TerminalLogLevel = line.level ?? "info";
          const isLast = index === lines.length - 1;

          return (
            <TypewriterRow
              key={line.id}
              text={line.text}
              level={level}
              animate={Boolean(typewriter && isLast)}
              charDelayMs={charDelayMs}
            />
          );
        })}
      </div>
    </div>
  );
}

export default TerminalLog;
