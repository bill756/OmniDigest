# OmniDigest · 跨平台内容智能脱水与动态事实核查智能体平台

针对微信公众号、知乎专栏等平台长图文内容「信息密度低、阅读成本高、夸大虚假难以甄别」的痛点，OmniDigest 提供**文章秒级解析、核心逻辑骨架提炼、可疑断言自动化联网核验**与**流式报告生成**的完整闭环。

系统依托 FastAPI + asyncio 异步底座，通过 LangGraph 编排带条件分支与反思机制的智能体工作流，配合 Redis 语义缓存与 PostgreSQL/pgvector（开发模式默认 SQLite）混合存储，实现兼具高吞吐、低推理成本与可信事实溯源的工程落地。

## 核心特性

- **高鲁棒性跨模态文本摄入** — 针对微信 / 知乎构建专用解析路由，结合 `trafilatura` 与规则引擎剔除导航、广告、评论等噪声 DOM，输出纯净 Markdown 正文。
- **按需动态事实核查**（Conditional Fact-Checking）— 仅对包含硬性数据、医疗健康、前沿科技等可核验断言触发 Tavily / DuckDuckGo 实时搜索比对，生成红黄绿三档置信度核验报告，避免无脑全量搜索的高开销。
- **多级缓存与防击穿** — Redis 缓存已解析 URL 及正文语义哈希，热点文章毫秒级回包，阻断重复爬取与大模型重复推理。
- **全链路 SSE 流式推流** — 从「爬取正文」「断言解析」「搜索比对」到「最终报告生成」，全过程通过打字机效果逐步渲染，无需忍受长推理白屏。
- **双轨持久化与语义反查** — 全文快照与元数据落库，核验摘要经向量嵌入后支持跨文章自然语言语义检索（pgvector）。

## 技术栈

| 模块 | 选型 |
| --- | --- |
| Web 异步底座 | FastAPI + Uvicorn + Pydantic |
| 智能体编排 | LangGraph（StateGraph 条件分支 + 反思重试） |
| 文本提取 | trafilatura + httpx + BeautifulSoup |
| 缓存 | Redis（滑动窗口限流 + 结果缓存） |
| 存储 | SQLAlchemy 2.0 async · PostgreSQL/pgvector（生产）· SQLite/aiosqlite（开发默认） |
| LLM | 任意 OpenAI 规范服务（DeepSeek / 通义千问 / 智谱 / OpenAI…） |
| 联网核查 | Tavily API（可选，缺省降级 DuckDuckGo） |

## 快速开始

### 1. 本地开发（零门槛 SQLite 模式）

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env             # 填入你的 OPENAI_API_KEY

# 启动服务
uvicorn app.main:app --reload --port 8000
```

打开 <http://localhost:8000> 即可使用工作台；接口文档见 <http://localhost:8000/docs>。

> 本地默认使用 SQLite（`omnidigest.db`），无需安装 Postgres / Redis（Redis 不可用时自动降级为无缓存模式）。pgvector 语义反查需切换到 PostgreSQL。

### 2. Docker Compose 一键部署（生产模式）

```bash
cp .env.example .env   # 填入 OPENAI_API_KEY 等配置

docker compose up -d --build
```

自动拉起 App + pgvector/pg16 + Redis 三个服务。

## 使用说明

1. **智能脱水分析** — 粘贴微信 / 知乎 / 任意公开文章 URL，一键运行。知乎、微信等强风控平台的文章可点击「直接粘贴正文」降级体验完整流程。
2. **事实核验** — 每项关键断言标注 🟢 证据吻合 / 🟡 存疑待验证 / 🔴 违背事实，附核验依据与置信度分布统计。
3. **语义反查与历史** — 对已落库文章用自然语言跨篇检索（如「固态电池有什么最新技术突破？」）。

> 页面打开时默认展示一份内置示例数据（无需后端即可完整演示），发起真实分析后自动切换为实时结果。

## 项目结构

```text
app/
├── main.py               # 应用生命周期、CORS 与路由注册
├── config.py             # 环境变量驱动的配置中心 (pydantic-settings)
├── api/v1/               # /digest /search /history 路由与依赖注入
├── core/                 # API Key 鉴权、SSE 事件封装
├── crawler/              # URL 分发器 + 微信/知乎/通用清洗适配器
├── agent/                # LangGraph 状态图、断言提取/路由/核验/合成节点
├── db/                   # 异步引擎、ORM 模型、pgvector 向量存取
└── schemas/              # Pydantic 请求/响应模型
```

## 配置项

见 [.env.example](.env.example)。关键项：

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `OPENAI_API_KEY` | LLM 服务密钥（必填） | — |
| `OPENAI_BASE_URL` | OpenAI 规范服务地址 | DeepSeek |
| `MODEL_NAME` | 模型名 | `deepseek-chat` |
| `TAVILY_API_KEY` | 联网核查搜索（可选） | 缺省用 DuckDuckGo |
| `DATABASE_URL` | 数据库连接串 | SQLite |
| `REDIS_URL` | Redis 地址 | `localhost:6379/0` |

## License

仅供学习与研究使用。
