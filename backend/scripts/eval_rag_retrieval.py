"""B4: RAG 检索质量评测（golden question → 期望命中政策）。

用法（在 backend 目录下）：
    python -m scripts.eval_rag_retrieval

对每条 golden question 调用 retrieve_policy_chunks，统计：
- hit@1 / hit@k：期望政策是否出现在第 1 位 / 前 k 位
- MRR：期望政策命中位置的平均倒数排名

退出码：hit@k 低于阈值（默认 0.8）时返回 1，可直接接入 CI。
"""

from __future__ import annotations

import asyncio
import sys

# Golden set：问题 → 期望命中的政策 ID。
# 新增政策或改写政策文案后必须补充/复核这里，防止检索质量静默回退。
GOLDEN_SET: list[tuple[str, str]] = [
    ("七天无理由退款怎么算", "P001"),
    ("收到商品的时候已经破损了能退吗", "P002"),
    ("商家发错货了怎么办", "P003"),
    ("商品用了几天就出质量问题，怎么退", "P004"),
    ("物流显示签收了但我没收到货", "P005"),
    ("退款金额超过500元需要人工审批吗", "P006"),
    ("退款一般多久能到账", "P007"),
    ("什么情况下不支持退款", "P008"),
    ("退货运费由谁承担", "P009"),
]

TOP_K = 3
HIT_AT_K_THRESHOLD = 0.8


async def run_eval() -> int:
    from app.agent.rag_service import retrieve_policy_chunks

    hits_at_1 = 0
    hits_at_k = 0
    reciprocal_ranks: list[float] = []
    failures: list[str] = []

    for query, expected in GOLDEN_SET:
        results = await retrieve_policy_chunks(query, top_k=TOP_K)
        ranked_ids = [r.policy_id for r in results]
        if expected in ranked_ids:
            rank = ranked_ids.index(expected) + 1
            hits_at_k += 1
            if rank == 1:
                hits_at_1 += 1
            reciprocal_ranks.append(1.0 / rank)
            print(f"  ✅ [{expected} @ rank {rank}] {query} -> {ranked_ids}")
        else:
            reciprocal_ranks.append(0.0)
            failures.append(query)
            print(f"  ❌ [{expected} missed ] {query} -> {ranked_ids}")

    total = len(GOLDEN_SET)
    hit1 = hits_at_1 / total
    hitk = hits_at_k / total
    mrr = sum(reciprocal_ranks) / total
    print(
        f"\nRAG retrieval eval: hit@1={hit1:.2%}  hit@{TOP_K}={hitk:.2%}  "
        f"MRR={mrr:.3f}  ({total} golden questions)"
    )
    if failures:
        print("Missed queries:")
        for q in failures:
            print(f"  - {q}")

    if hitk < HIT_AT_K_THRESHOLD:
        print(f"\nFAIL: hit@{TOP_K} {hitk:.2%} < threshold {HIT_AT_K_THRESHOLD:.0%}")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run_eval()))
