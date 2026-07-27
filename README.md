# 售后智能客服 Agent — POC

基于 **LangGraph + RAG** 的多轮对话售后客服系统，支持意图识别、知识库检索、引用溯源、工单创建与多语言服务。

## 技术栈

| 层 | 技术 |
|---|------|
| LLM | DeepSeek API（OpenAI 兼容） |
| Embedding | 硅基流动 BAAI/bge-m3（1024 维） |
| Agent 框架 | LangGraph 0.4 + AsyncPostgresSaver |
| 后端 | FastAPI + SSE 流式推送 |
| 前端 | Next.js 15 + React 19 + Tailwind 4 |
| 数据库 | PostgreSQL 16 + pgvector（HNSW） |
| 部署 | Docker Compose 一键启动 |

## 架构概览

```
┌─────────────────────────────────────────────────┐
│                   Frontend (Next.js)             │
│   SSE 流式渲染 / 引用卡片 / 工单弹窗 / 会话恢复   │
└────────────────────────┬────────────────────────┘
                         │ POST /api/chat (SSE)
┌────────────────────────▼────────────────────────┐
│                  Backend (FastAPI)                │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │           LangGraph StateGraph            │   │
│  │                                           │   │
│  │  START → intent_router → route_decision   │   │
│  │              ↙   ↓   ↓   ↘               │   │
│  │      clarify repeat redirect retrieve     │   │
│  │                              ↓            │   │
│  │                           grade           │   │
│  │                          ↙     ↘          │   │
│  │                   generate   fallback     │   │
│  │                      ↓          ↓         │   │
│  │                         END               │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌─────────────┐  ┌─────────────────────────┐   │
│  │  LLM 适配层  │  │  RAG: 混合检索 (RRF)    │   │
│  │  (OpenAI SDK)│  │  pgvector + tsvector    │   │
│  └─────────────┘  └─────────────────────────┘   │
└────────────────────────┬────────────────────────┘
                         │
┌────────────────────────▼────────────────────────┐
│          PostgreSQL 16 + pgvector                │
│   kb_chunks (向量+全文) │ tickets │ checkpoints  │
└─────────────────────────────────────────────────┘
```

## 核心能力

1. **意图路由**：单次结构化输出判断 intent / language / emotion / product_line / clarify / repeat + 指代消解
2. **混合检索**：pgvector HNSW 向量 + jieba 预分词 tsvector 全文 → RRF 融合
3. **两级评估**：产品线元数据校验 → 分数短路 → LLM 精评（三段递进零浪费）
4. **引用溯源**：生成答案中 `[i]` 标记自动映射到 citation 帧
5. **重复识别**：answered_topics 机制 + is_repeat 路由 → 避免重复回答
6. **知识库盲区兜底**：改写重试一次 → 坦诚告知 → suggest 转人工工单
7. **多语言**：中英文知识库 + 自动识别语言 + 同语言回复
8. **SSE 8 帧协议**：status / thinking / tool / delta / citation / suggest / done / error

## 快速启动

### 前置条件

- Docker & Docker Compose
- Node.js 20+ & pnpm
- DeepSeek API Key
- 硅基流动 API Key（免费注册即可）

### 步骤

```bash
# 1. 克隆并进入项目
cd aftersales-agent

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY 和 EMBEDDING_API_KEY

# 3. 启动后端服务（含数据库）
docker compose up -d

# 4. 索引知识库
curl -X POST http://localhost:8000/api/kb/reindex

# 5. 启动前端
cd frontend && pnpm install && pnpm dev
# 浏览器打开 http://localhost:3003
```

### 验证

```bash
# 健康检查
curl http://localhost:8000/healthz

# 知识库状态
curl http://localhost:8000/api/kb/stats

# 对话测试
curl -N -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo","message":"笔记本电池充不进电怎么办？"}'
```

## 项目结构

```
aftersales-agent/
├── backend/
│   ├── app/
│   │   ├── agent/          # LangGraph 状态图
│   │   │   ├── graph.py    # 图拓扑定义
│   │   │   ├── nodes.py    # 节点实现（核心逻辑）
│   │   │   ├── prompts.py  # Prompt 模板
│   │   │   └── state.py    # AgentState 定义
│   │   ├── api/            # FastAPI 路由
│   │   │   ├── chat.py     # SSE 对话端点
│   │   │   ├── kb.py       # 知识库管理
│   │   │   └── ticket.py   # 工单 API
│   │   ├── llm/
│   │   │   └── client.py   # 模型适配层
│   │   ├── rag/
│   │   │   ├── ingest.py   # 知识库摄取管线
│   │   │   └── store.py    # 混合检索实现
│   │   ├── config.py       # 配置管理
│   │   └── main.py         # 应用入口
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── components/Chat.tsx  # 聊天主组件
│       ├── lib/sse.ts           # SSE 解析器
│       └── app/                 # Next.js 页面
├── knowledge/              # 知识库语料
│   ├── zh/                 # 中文文档
│   └── en/                 # 英文文档
├── docker-compose.yml
├── .env.example
└── demo.sh                 # 演示脚本
```

## SSE 帧协议

| 事件 | 用途 | 数据示例 |
|------|------|----------|
| `status` | 阶段通知 | `{"stage":"routing"}` |
| `thinking` | 推理过程 | `{"text":"意图=troubleshoot ..."}` |
| `tool` | 工具调用 | `{"name":"hybrid_search","hits":[...]}` |
| `delta` | 流式文本 | `{"text":"您好"}` |
| `citation` | 引用溯源 | `{"items":[{"title":"...","snippet":"..."}]}` |
| `suggest` | 建议操作 | `{"items":["转人工"],"action":"ticket"}` |
| `done` | 对话结束 | `{"session_id":"..."}` |
| `error` | 错误信息 | `{"message":"...","code":"..."}` |

## 设计亮点

- **产品线校验先行**：阻止"服务器保修"词面命中"笔记本保修"文档的误判
- **jieba 预分词方案**：无需安装 PostgreSQL 中文分词扩展（zhparser/pg_jieba），部署零依赖
- **延迟控制**：路由一次结构化调用 6 项判断；grade 规则短路避免 90% 场景的 LLM 调用
- **Prompt 注入防护**：域外意图识别 + system prompt 角色锁定，经测试抵御 DAN/越狱攻击
- **会话持久化**：LangGraph AsyncPostgresSaver 自动管理，刷新页面会话不丢失
