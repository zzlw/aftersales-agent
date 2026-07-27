import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // /api/* 已全部由 Route Handler 代理（chat/history/ticket），不再需要 rewrites
  // Next.js 16: 启用 React Compiler 自动优化 re-render
  reactCompiler: true,
};

export default nextConfig;
