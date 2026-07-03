"""Dependency-free concurrent smoke benchmark for the config-driven runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage

from app.agent.generic_runtime import run_configured_scenario


async def benchmark(requests: int, concurrency: int) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    durations: list[float] = []
    failures = 0

    async def run_one(index: int) -> None:
        nonlocal failures
        async with semaphore:
            started = time.perf_counter()
            try:
                result = await run_configured_scenario(
                    {
                        "messages": [HumanMessage(content="申请 GitHub 只读权限，用于查看项目代码")],
                        "user_id": f"load-user-{index}",
                        "user_role": "AGENT",
                        "tenant_id": "default",
                        "thread_id": f"runtime-load-{index}",
                        "trace_id": f"runtime-load-trace-{index}",
                    },
                    "permission_request",
                    dry_run=True,
                )
                if result.get("error_message"):
                    failures += 1
            except Exception:
                failures += 1
            finally:
                durations.append((time.perf_counter() - started) * 1000)

    suite_started = time.perf_counter()
    await asyncio.gather(*(run_one(index) for index in range(requests)))
    elapsed = time.perf_counter() - suite_started
    ordered = sorted(durations)

    def percentile(fraction: float) -> float:
        if not ordered:
            return 0.0
        return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]

    return {
        "profile": "config_runtime_dry_run",
        "requests": requests,
        "concurrency": concurrency,
        "successes": requests - failures,
        "failures": failures,
        "elapsed_seconds": round(elapsed, 4),
        "requests_per_second": round(requests / elapsed, 2) if elapsed else 0,
        "latency_ms": {
            "average": round(statistics.mean(durations), 3) if durations else 0,
            "p50": round(percentile(0.50), 3),
            "p95": round(percentile(0.95), 3),
            "max": round(max(durations), 3) if durations else 0,
        },
        "scope": "In-process deterministic runtime only; not a production capacity claim.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(benchmark(args.requests, args.concurrency)), indent=2))


if __name__ == "__main__":
    main()
