import type { Config } from "jest";

const config: Config = {
  testMatch: ["**/evals/**/*.test.ts"],
  transform: {
    "^.+\\.tsx?$": [
      "ts-jest",
      {
        tsconfig: {
          module: "commonjs",
          esModuleInterop: true,
          resolveJsonModule: true,
        },
      },
    ],
  },
  testEnvironment: "node",
  testTimeout: 60000,  // Agent 调用 LLM 最多等 60s
  verbose: true,
  // 每个测试串行运行，避免并发打乱 SSE 流
  maxWorkers: 1,
};

export default config;
