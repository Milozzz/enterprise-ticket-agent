"""Run the P0 release gates locally or in CI."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.agent.p0_evaluation import run_p0_eval_report


async def _run(output: str | None, use_llm_judge: bool) -> int:
    report = await run_p0_eval_report(use_llm_judge=use_llm_judge)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if output:
        Path(output).write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["status"] == "PASS" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Run P0 trajectory, RAG, safety, and judge evals")
    parser.add_argument("--output", help="Optional JSON report path")
    parser.add_argument("--llm-judge", action="store_true", help="Use Gemini as judge")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.output, args.llm_judge)))


if __name__ == "__main__":
    main()
