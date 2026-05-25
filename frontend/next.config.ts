import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  experimental: {
    ppr: false,
  },
  // 暂时移除 turbopack 配置以解决 Windows 下的样式加载问题
  /*
  turbopack: {
    root: __dirname,
  },
  */
  async rewrites() {
    return [
      {
        source: "/api/backend/:path*",
        destination: `${process.env.BACKEND_URL || "http://localhost:8000"}/:path*`,
      },
    ];
  },
};

export default nextConfig;
