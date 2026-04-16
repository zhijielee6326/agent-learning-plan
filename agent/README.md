# 保险产品精算智能体

基于 RAG（检索增强生成）的保险公司内部产品条款检索、生成与溯源系统。

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                      Frontend (Next.js)                  │
│            React + TypeScript + Tailwind CSS             │
│                  http://localhost:3000                    │
├─────────────────────────────────────────────────────────┤
│                       Backend (FastAPI)                   │
│                  http://localhost:8000                    │
├──────────┬──────────────┬───────────────┬────────────────┤
│ LLM Agent│ Clause Draft │ Vector Store  │ Product Store  │
│ (RAG+LLM)│   Agent      │  (Qdrant)     │  (JSON/CSV)    │
├──────────┴──────────────┴───────────────┴────────────────┤
│       Ollama (Embedding + Reranker)  │  LLM API (GLM)    │
└─────────────────────────────────────┴────────────────────┘
```

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 | Next.js 14 + React 18 + TypeScript | 单页应用，SSE 流式对话 |
| UI | Tailwind CSS | 类豆包 AI 编辑界面 |
| 后端 | Python + FastAPI | RESTful API + SSE 流式输出 |
| 向量数据库 | Qdrant | 保险条款语义检索 |
| Embedding | Ollama (Qwen3-Embedding) | 文本向量化 |
| Reranker | Ollama (Qwen3-Reranker) | 检索结果重排序 |
| LLM | GLM-5.1 (Anthropic 兼容接口) | 智能对话与条款生成 |

## 核心功能

1. **智能对话** — LLM 驱动的多轮对话，支持日常闲聊和专业保险分析
2. **RAG 检索** — 基于 Qdrant 向量语义搜索 + Reranker 重排序，精准匹配条款
3. **条款溯源** — 每条检索结果自动附加 `[产品名称 > 章节名称 > 条款标题]` 标注
4. **流式生成** — SSE 实时流式输出，打字机动画效果
5. **条款起草** — 基于产品库智能生成新条款（意图识别 → 语义检索 → 逐章生成）
6. **AI 辅助编辑** — 选中文本后支持润色、简写、扩写、改写、翻译等 AI 操作
7. **多产品组合** — 支持多产品条款组合分析，自动统一结构
8. **导出功能** — 支持 Markdown 和 Word (.docx) 格式导出
9. **会话管理** — 多会话切换，自动持久化到 localStorage

## 快速启动

### 前置依赖

- Python 3.10+
- Node.js 18+
- Docker（用于运行 Qdrant 和 Ollama）
- Ollama（需部署 Qwen3-Embedding 和 Qwen3-Reranker 模型）

### 1. 启动 Qdrant

```bash
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant
```

### 2. 启动后端

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env   # 编辑 .env 配置 LLM API 和 Ollama 地址
python main.py
```

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

### 4. 访问系统

打开浏览器访问 http://localhost:3000

## 项目结构

```
agent/
├── backend/                    # Python 后端
│   ├── main.py                # FastAPI 主入口，API 路由定义
│   ├── product_store.py       # 产品库加载与关键词检索引擎
│   ├── clause_generator.py    # 结构化条款生成（规则引擎）
│   ├── llm_agent.py           # RAG 智能对话 Agent（向量检索 + LLM）
│   ├── clause_draft_agent.py  # 条款起草 Agent（意图识别 → 逐章生成）
│   ├── vector_store.py        # Qdrant 向量存储与语义检索
│   ├── data/
│   │   ├── products.json      # 产品主数据
│   │   ├── output/            # 188 份备案产品条款 JSON
│   │   └── pfa_actuarial_*.csv # 精算产品数据（1600+ 款）
│   └── requirements.txt
├── frontend/                   # Next.js 前端
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx       # 主页面（左右分栏：聊天 + 文档编辑）
│   │   │   ├── layout.tsx     # 根布局
│   │   │   └── globals.css    # 全局样式（气泡、编辑器、AI 弹窗等）
│   │   └── lib/
│   │       ├── types.ts       # TypeScript 类型定义
│   │       ├── constants.ts   # API 地址、默认内容、快捷查询
│   │       └── markdown.ts    # Markdown → HTML 转换 & TOC 解析
│   └── package.json
└── README.md
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/products` | 获取产品列表 |
| POST | `/api/chat/stream` | LLM 智能对话（SSE 流式） |
| POST | `/api/chat` | LLM 智能对话（非流式） |
| POST | `/api/chat/clear` | 清除对话历史 |
| POST | `/api/search` | 关键词检索条款 |
| POST | `/api/generate/stream` | 规则引擎流式生成 |
| POST | `/api/clause-draft/stream` | 条款起草工作流（SSE） |
| POST | `/api/edit/stream` | AI 辅助编辑（SSE） |
| POST | `/api/combined` | 多产品条款组合 |
| POST | `/api/export/word` | 导出 Word 文档 |
| GET | `/api/chapters` | 获取标准章节列表 |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_TYPE` | LLM 接口类型 | `anthropic` |
| `LLM_API_KEY` | LLM API 密钥 | — |
| `LLM_MODEL` | LLM 模型名称 | `glm-5.1` |
| `LLM_BASE_URL` | LLM API 地址 | — |
| `QDRANT_URL` | Qdrant 地址 | `http://localhost:6333` |
| `QDRANT_COLLECTION` | Qdrant 集合名 | `insurance_clauses` |
| `OLLAMA_BASE_URL` | Ollama 地址 | `http://localhost:11434/v1` |
| `EMBEDDING_MODEL` | Embedding 模型 | `qwen3-embedding:8b` |
| `RERANKER_MODEL` | Reranker 模型 | `dengcao/Qwen3-Reranker-4B:Q8_0` |

## License

MIT
