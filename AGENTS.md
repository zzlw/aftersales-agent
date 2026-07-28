# AGENTS.md

面向 AI 编码代理的项目约束文件。人类贡献者请先读 [README.md](README.md)。

## 项目概览

售后智能客服 Agent POC：LangGraph 多轮对话 + RAG 引用溯源 + 工单创建。

- `backend/` — Python 3.12 + FastAPI + LangGraph 0.4 + PGVector，SSE 流式输出
- `frontend/` — Next.js 16（App Router + Turbopack + React Compiler）+ React 19 + Tailwind CSS v4 + shadcn/ui
- `knowledge/` — RAG 知识库 Markdown 源文件（zh / en 双语）
- 部署：前端 Vercel（push main 自动部署），本地全栈用 Docker Compose

## 环境与命令

```bash
# 全栈启动（Postgres + backend + frontend）
docker compose up -d

# 前端开发（frontend/ 目录，包管理器固定用 pnpm）
pnpm install
pnpm dev          # 默认 3000；本地惯用 pnpm dev -p 3003
pnpm build        # 提交前必须通过

# 后端开发（backend/ 目录，uv 项目模式）
uv sync
uv run --env-file ../.env uvicorn app.main:app --reload --port 8000

# 知识库重建索引
uv run --env-file ../.env python -m app.rag.ingest
```

- 环境变量以 `.env.example` 为准；`BACKEND_URL` 是前端 Route Handler 代理后端的地址（默认 `http://localhost:8000`）。

## 安全红线（必须遵守）

- **绝不提交密钥**：`.env` 已被 gitignore，任何真实 API Key 只能放 `.env`；新增配置项时同步更新 `.env.example`（用 `sk-your-xxx-key` 占位符）。
- `docs/` 目录含个人材料，已被 gitignore，不得移入仓库跟踪范围。
- 前端代码中不得出现 `NEXT_PUBLIC_` 前缀的敏感值；后端地址只经服务端 Route Handler 转发。

## 前端约束

### 架构

- 最大化 SSR：`page.tsx` 是 async Server Component（`cookies()` 读 `session_id`、服务端预取历史）；`Chat.tsx` 是唯一的大型 Client Component。
- `/api/chat`、`/api/history/[sid]`、`/api/ticket` 均为 Route Handler 代理，**不使用** `next.config.ts` rewrites。
- SSE 帧协议：`status / thinking / tool / delta / citation / suggest / error / done`，修改前后端任何一侧的帧格式必须同步另一侧。

### UI 体系（不得偏离）

- 组件一律用 shadcn/ui（`src/components/ui/`），新组件用 `pnpm dlx shadcn@latest add <name>` 安装，不手写平行组件体系。
- 唯一 accent 色：联想红 `#E2231A`（`--primary`），中性色 zinc 系，圆角基准 `--radius: 0.75rem`，全部经 `globals.css` 语义 token 使用，**不得硬编码色值**。
- 字体：Geist Sans / Geist Mono（禁用 Inter）；图标只用 lucide-react 一个图标族。
- 动画一律用 `motion/react`（不是 `framer-motion` 包名）：只动 transform/opacity，spring 参数参考 `springIn = { type: "spring", stiffness: 320, damping: 28 }`，所有动画必须 `useReducedMotion()` 降级。
- 滚动容器用 shadcn ScrollArea（Radix），不引入第二套滚动条库。
- 全屏布局用 `h-dvh`（不是 `h-screen`）。
- shimmer（`shadcn/tailwind.css` 工具类）只用于"正在流式输出"的状态文字，不得用于静态内容。

### Tailwind v4 Preflight 已知坑（globals.css 已兜底，不要重复处理）

- 按钮光标：base 层已全局恢复 `cursor: pointer`（`:not(:disabled)`），**不要**再给按钮加 `cursor-pointer` 类。
- 默认边框色：base 层已有 `* { border-border }`，新元素勿依赖 currentColor 边框。

## 后端约束

- LangGraph 节点在 `app/agent/nodes.py`，图结构在 `app/agent/graph.py`，状态定义在 `app/agent/state.py`；新增节点必须三处同步。
- Prompt 全部集中在 `app/agent/prompts.py`，不散落在节点代码里。
- LLM 调用统一走 `app/llm/client.py`（OpenAI 兼容，Provider 可切换），不直接 import openai。
- 会话状态用 AsyncPostgresSaver checkpointer，不引入内存态会话存储。

## 验收标准

- 前端改动：`pnpm build` 零错误；涉及 UI 的改动需在浏览器实测（含 SSE 对话流程）。
- 后端改动：`docker compose up` 后 `/api/chat` SSE 全链路可用。
- 提交信息用中文或英文均可，格式 `<type>: <subject>`（feat / fix / refactor / docs / chore）。
