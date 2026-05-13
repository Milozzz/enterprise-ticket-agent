import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import("next").NextConfig} */
const nextConfig = {
  // standalone 模式：产物自包含，适合 Docker 部署（不需要完整 node_modules）
  output: "standalone",
  // 避免检测到上级目录里的 package-lock 后误判 workspace 根（Next 会提示多 lockfile）。
  outputFileTracingRoot: path.join(__dirname),
};

export default nextConfig;
