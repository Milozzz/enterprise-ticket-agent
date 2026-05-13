"""
eval_classifier.py — 意图分类评估脚本

用法：
    cd backend
    python -m scripts.eval_classifier

    # 只跑规则引擎（不消耗 API 额度）：
    python -m scripts.eval_classifier --rule-only

输出：
    - 总准确率
    - 每条用例的预测结果
    - 误分类分析
    - 可复制到简历的一句话结论
"""

import asyncio
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.nodes.classifier import _llm_classify, _rule_classify

# ── 测试用例（20 条，覆盖边界场景）────────────────────────────────────────
TEST_CASES = [
    # ── 退款申请（明确）
    {"input": "订单123456申请退款，商品破损",               "expected": "refund",       "tag": "退款-破损"},
    {"input": "我要退款，收到的商品是坏的",                  "expected": "refund",       "tag": "退款-破损无单号"},
    {"input": "订单789012发错货了，申请退货",                "expected": "refund",       "tag": "退款-发错"},
    {"input": "这个东西质量太差了，我要退钱",                "expected": "refund",       "tag": "退款-质量"},
    {"input": "快递显示已签收但我没收到，要退款",             "expected": "refund",       "tag": "退款-未收到"},

    # ── 订单查询
    {"input": "帮我查一下订单456789现在到哪里了",            "expected": "query_order",  "tag": "查询-物流"},
    {"input": "我的退款申请处理到哪一步了",                  "expected": "query_order",  "tag": "查询-进度"},
    {"input": "订单345678的状态是什么",                      "expected": "query_order",  "tag": "查询-状态"},
    {"input": "我上次申请的退款什么时候能到账",               "expected": "query_order",  "tag": "查询-到账"},

    # ── 政策咨询（容易和退款混淆）
    {"input": "七天无理由退款运费谁出",                      "expected": "query_policy", "tag": "政策-运费"},
    {"input": "退款要多久才能到账",                          "expected": "query_policy", "tag": "政策-时效"},
    {"input": "什么情况下可以申请退款",                      "expected": "query_policy", "tag": "政策-条件"},
    {"input": "超过七天了还能退吗",                          "expected": "query_policy", "tag": "政策-超期"},
    {"input": "退款政策是怎么规定的",                        "expected": "query_policy", "tag": "政策-通用"},

    # ── 边界歧义（最难分的）
    {"input": "我想退款，请问需要多久",                      "expected": "query_policy", "tag": "歧义-退款+时效"},
    {"input": "破损商品可以退吗",                            "expected": "query_policy", "tag": "歧义-政策询问"},
    {"input": "订单号123456，退款规则是什么",                 "expected": "query_policy", "tag": "歧义-有单号+政策"},
    {"input": "我要查一下退款的条件",                        "expected": "query_policy", "tag": "歧义-查询+政策"},

    # ── 其他
    {"input": "你好，你能帮我做什么",                        "expected": "other",        "tag": "其他-问候"},
    {"input": "我想投诉这家店的服务态度",                    "expected": "other",        "tag": "其他-投诉"},
]


def print_result_row(i: int, case: dict, predicted: str, correct: bool):
    icon  = "✓" if correct else "✗"
    color = "" if correct else "  ← 误分类"
    print(f"  {icon} [{i:02d}] {case['tag']:<18} 预测={predicted:<14} 期望={case['expected']}{color}")


async def run_llm_eval() -> tuple[int, int]:
    """用 LLM 跑一遍，返回 (correct, total)"""
    correct = 0
    wrong_cases = []

    print("\n  [LLM 模式] 调用 Gemini，请稍候（每次间隔 2s 避免 rate limit）...\n")
    for i, case in enumerate(TEST_CASES, 1):
        try:
            result  = await _llm_classify(case["input"])
            predicted = result.get("intent", "unknown")
        except Exception as e:
            predicted = f"ERROR({e})"
        # 避免触发 Gemini rate limit（免费版 15 RPM）
        await asyncio.sleep(2)

        ok = predicted == case["expected"]
        if ok:
            correct += 1
        else:
            wrong_cases.append((i, case, predicted))
        print_result_row(i, case, predicted, ok)

    return correct, len(TEST_CASES), wrong_cases


def run_rule_eval() -> tuple[int, int, list]:
    """用规则引擎跑一遍，不消耗 API 额度"""
    correct = 0
    wrong_cases = []

    print("\n  [规则引擎模式] 无需 API，本地执行\n")
    for i, case in enumerate(TEST_CASES, 1):
        result    = _rule_classify(case["input"])
        predicted = result.get("intent", "unknown")
        ok        = predicted == case["expected"]
        if ok:
            correct += 1
        else:
            wrong_cases.append((i, case, predicted))
        print_result_row(i, case, predicted, ok)

    return correct, len(TEST_CASES), wrong_cases


def print_summary(correct: int, total: int, wrong_cases: list, mode: str):
    acc = correct / total * 100
    width = 60
    print(f"\n{'═' * width}")
    print(f"  {mode} 评估结果")
    print(f"{'═' * width}")
    print(f"  准确率：{correct}/{total} = {acc:.1f}%")

    if wrong_cases:
        print(f"\n  误分类详情（{len(wrong_cases)} 条）：")
        for i, case, predicted in wrong_cases:
            print(f"    [{i:02d}] 输入：{case['input'][:30]}...")
            print(f"          预测：{predicted}  期望：{case['expected']}")
            print(f"          标签：{case['tag']}")

    # 按意图统计准确率
    intent_total:   dict[str, int] = {}
    intent_correct: dict[str, int] = {}
    for i, case in enumerate(TEST_CASES, 1):
        exp = case["expected"]
        intent_total[exp]   = intent_total.get(exp, 0) + 1
        # 如果没有在 wrong_cases 里找到这条，就是正确的
        is_wrong = any(w[0] == i for w in wrong_cases)
        if not is_wrong:
            intent_correct[exp] = intent_correct.get(exp, 0) + 1

    print(f"\n  各意图准确率：")
    for intent in sorted(intent_total.keys()):
        c = intent_correct.get(intent, 0)
        t = intent_total[intent]
        print(f"    {intent:<20} {c}/{t} = {c/t*100:.0f}%")

    # 给简历用的一句话结论
    print(f"\n{'─' * width}")
    print(f"  简历结论（可直接使用）：")
    print(f"  构建意图分类评估集（{total} 个用例，覆盖退款/查询/政策/边界歧义场景），")
    print(f"  {mode}准确率 {acc:.0f}%。")
    print(f"{'─' * width}\n")


async def main():
    parser = argparse.ArgumentParser(description="意图分类评估脚本")
    parser.add_argument("--rule-only", action="store_true", help="只跑规则引擎，不调用 LLM")
    args = parser.parse_args()

    print(f"\n{'═' * 60}")
    print("  意图分类评估")
    print(f"  用例数：{len(TEST_CASES)}")
    print(f"{'═' * 60}")

    if args.rule_only:
        correct, total, wrong = run_rule_eval()
        print_summary(correct, total, wrong, "规则引擎")
    else:
        correct, total, wrong = await run_llm_eval()
        print_summary(correct, total, wrong, "LLM")

        # 对比规则引擎
        print(f"{'─' * 60}")
        print("  对比：规则引擎")
        print(f"{'─' * 60}")
        r_correct, r_total, r_wrong = run_rule_eval()
        print_summary(r_correct, r_total, r_wrong, "规则引擎")

        print(f"  LLM 比规则引擎高 {(correct - r_correct) / total * 100:+.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
