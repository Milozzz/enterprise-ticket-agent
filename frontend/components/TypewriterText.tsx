"use client";

import { useEffect, useState, useRef } from "react";
import { cn } from "@/lib/utils";

function randomDelay(minMs: number, maxMs: number) {
  return minMs + Math.random() * (maxMs - minMs);
}

export interface TypewriterTextProps {
  text: string;
  /** 为 true 时立即显示全文，不播放打字机 */
  skip?: boolean;
  charMinMs?: number;
  charMaxMs?: number;
  className?: string;
  onComplete?: () => void;
}

/**
 * 逐字打印（每字符间隔在 charMinMs–charMaxMs 间随机），末尾块状光标 █，结束后消失。
 */
export function TypewriterText({
  text,
  skip = false,
  charMinMs = 20,
  charMaxMs = 50,
  className,
  onComplete,
}: TypewriterTextProps) {
  const [visibleCount, setVisibleCount] = useState(skip ? text.length : 0);
  const [done, setDone] = useState(skip);
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  useEffect(() => {
    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];

    if (skip) {
      setVisibleCount(text.length);
      setDone(true);
      onCompleteRef.current?.();
      return;
    }

    setVisibleCount(0);
    setDone(false);

    let i = 0;
    const step = () => {
      if (i >= text.length) {
        setDone(true);
        onCompleteRef.current?.();
        return;
      }
      i += 1;
      setVisibleCount(i);
      const delay = randomDelay(charMinMs, charMaxMs);
      const t = setTimeout(step, delay);
      timersRef.current.push(t);
    };

    const first = setTimeout(step, randomDelay(charMinMs, charMaxMs));
    timersRef.current.push(first);

    return () => {
      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
    };
  }, [text, skip, charMinMs, charMaxMs]);

  const visible = text.slice(0, visibleCount);

  return (
    <span className={cn("inline font-mono text-[11px] font-semibold tracking-tight", className)}>
      <span>{visible}</span>
      {!done ? (
        <span
          className="ml-px inline-block min-w-[0.5em] animate-pulse font-mono text-current align-baseline"
          aria-hidden
        >
          █
        </span>
      ) : null}
    </span>
  );
}
