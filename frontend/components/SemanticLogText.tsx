"use client";

import { cn } from "@/lib/utils";

const ERROR_REGEX =
  /(失败|错误|异常|无法完成|拒绝|宕机|\bfail(?:ed|ure)?\b|\berror\b|Error|ERROR)/gi;
const WARN_REGEX =
  /(挂起|审批|待审|人工|警告|风控|阈值|复核|等待主管|超过|WARN|\bwarn\b)/gi;

export interface SemanticLogTextProps {
  text: string;
  className?: string;
}

type Tone = "default" | "warn" | "error";

/**
 * 默认 text-slate-700；Warn 词汇 text-amber-500；错误类 text-red-500。
 * 先按 ERROR 切分，剩余片段再按 WARN 切分。
 */
export function SemanticLogText({ text, className }: SemanticLogTextProps) {
  const segments = splitByErrorThenWarn(text);
  return (
    <span
      className={cn(
        "text-[11px] font-mono leading-relaxed break-all",
        className
      )}
    >
      {segments.map((seg, i) => (
        <span
          key={i}
          className={cn(
            seg.tone === "error" && "text-red-500",
            seg.tone === "warn" && "text-amber-500",
            seg.tone === "default" && "text-slate-700"
          )}
        >
          {seg.s}
        </span>
      ))}
    </span>
  );
}

function splitByErrorThenWarn(input: string): { s: string; tone: Tone }[] {
  const out: { s: string; tone: Tone }[] = [];
  let rest = input;

  while (rest.length > 0) {
    ERROR_REGEX.lastIndex = 0;
    const em = ERROR_REGEX.exec(rest);
    if (!em) {
      out.push(...splitWarnOnly(rest));
      break;
    }
    if (em.index > 0) {
      out.push(...splitWarnOnly(rest.slice(0, em.index)));
    }
    out.push({ s: em[0], tone: "error" });
    rest = rest.slice(em.index + em[0].length);
  }

  return out.length ? out : [{ s: input, tone: "default" }];
}

function splitWarnOnly(chunk: string): { s: string; tone: Tone }[] {
  if (!chunk) return [];
  const out: { s: string; tone: Tone }[] = [];
  let rest = chunk;

  while (rest.length > 0) {
    WARN_REGEX.lastIndex = 0;
    const wm = WARN_REGEX.exec(rest);
    if (!wm) {
      out.push({ s: rest, tone: "default" });
      break;
    }
    if (wm.index > 0) {
      out.push({ s: rest.slice(0, wm.index), tone: "default" });
    }
    out.push({ s: wm[0], tone: "warn" });
    rest = rest.slice(wm.index + wm[0].length);
  }

  return out;
}
