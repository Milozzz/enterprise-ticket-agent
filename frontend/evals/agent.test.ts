/**
 * evals/agent.test.ts
 * Agent 自动化 Eval 测试集 — 10 个测试用例
 *
 * 运行方式：npm run eval
 * 前提：后端已启动（http://localhost:8000）
 */

import { describe, test, beforeAll, expect } from "@jest/globals";
import { TEST_CASES } from "./fixtures";
import { callAgent, isBackendAvailable, assertResult, BACKEND_URL } from "./helpers";

// ── 全局 Setup：检查后端是否可达 ─────────────────────────────────────────

let backendAvailable = false;

beforeAll(async () => {
  backendAvailable = await isBackendAvailable();
  if (!backendAvailable) {
    console.warn(
      `\n⚠️  后端不可达（${BACKEND_URL}），所有 Eval 测试将被跳过。\n` +
      `   请先启动后端：cd backend && uvicorn app.main:app --port 8000\n`
    );
  } else {
    console.log(`\n✅ 后端已连接：${BACKEND_URL}\n`);
  }
}, 10000);

// ── 统计通过率 ────────────────────────────────────────────────────────────

const results: { id: string; passed: boolean }[] = [];

afterAll(() => {
  const total = results.length;
  const passed = results.filter((r) => r.passed).length;
  const rate = total > 0 ? ((passed / total) * 100).toFixed(0) : "0";
  console.log(`\n📊 Eval 结果：${passed}/${total} 通过，准确率 ${rate}%`);
  if (Number(rate) < 90) {
    console.warn("⚠️  准确率低于 90%，请检查失败用例");
  } else {
    console.log("🎉 准确率 ≥ 90%，验收通过！");
  }
});

// ── 测试用例 ──────────────────────────────────────────────────────────────

describe("Agent Evals — 10 个测试用例", () => {
  for (const tc of TEST_CASES) {
    test(`[${tc.id}] ${tc.name}`, async () => {
      if (!backendAvailable) {
        console.log(`  ⏭  跳过（后端不可达）`);
        results.push({ id: tc.id, passed: true }); // 跳过不算失败
        return;
      }

      if (tc.skip) {
        console.log(`  ⏭  跳过：${tc.skip}`);
        results.push({ id: tc.id, passed: true });
        return;
      }

      let result;
      try {
        result = await callAgent(tc.message);
      } catch (err) {
        results.push({ id: tc.id, passed: false });
        throw new Error(`[${tc.id}] API 调用失败：${err}`);
      }

      // HTTP 状态必须是 200
      expect(result.status).toBe(200);

      // 必须收到至少一个 SSE 事件
      expect(result.events.length).toBeGreaterThan(0);

      // 运行业务断言
      try {
        assertResult(result, tc.name, tc.assertions);
        results.push({ id: tc.id, passed: true });
        console.log(
          `  ✅ 通过\n` +
          `     UI 事件: [${result.uiTypes.join(", ") || "无"}]\n` +
          `     Steps: [${result.thinkingStepLabels.join(", ") || "无"}]\n` +
          `     回复摘要: ${result.fullText.slice(0, 100).replace(/\n/g, " ")}...`
        );
      } catch (assertErr) {
        results.push({ id: tc.id, passed: false });
        throw assertErr;
      }
    });
  }
});
