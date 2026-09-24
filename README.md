# 法律智能助手系统 (Legal Intelligent Assistance System)

基于 AI 大模型的中国法律智能助手平台，提供法律咨询、合同审查、文书生成和法律检索等智能服务。

## 项目概述

法律智能助手系统是一个面向中国法律实务场景的 AI 驱动平台，利用 LangChain/LangGraph 构建多智能体协作架构，通过 RAG（检索增强生成）技术提供精准的法律知识服务。系统支持自然语言法律咨询、合同风险智能审查、法律文书自动生成以及法律法规多策略检索，旨在降低法律服务的获取门槛，提升法律工作效率。

### 核心功能

| 功能模块 | 描述 |
|---------|------|
| **法律咨询** | 智能路由用户问题到专业法律咨询 Agent，提供法条引用、案例分析、实务建议 |
| **合同审查** | 上传合同文件（PDF/DOCX/TXT），AI 自动识别违约、知识产权、保密等 8 类风险 |
| **文书生成** | 支持民事起诉状、答辩状、律师函、法律意见书、仲裁申请书等 5+ 种文书模板 |
| **法律检索** | 多策略（语义+关键词）检索法律法规，涵盖宪法、刑法、民法典等 7 大类 |
| **数据飞轮** | 用户反馈分析、使用趋势统计、热门话题追踪，持续优化模型效果 |

## 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        用户层 (User Layer)                        │
│           Vue3 SPA  │  Nginx  │  REST API  │  WebSocket          │
├──────────────────────────────────────────────────────────────────┤
│                        API 网关 (FastAPI)                         │
│   认证 (JWT)  │  限流  │  CORS  │  文件上传  │  异常处理          │
├──────────────────────────────────────────────────────────────────┤
│                    智能体层 (Agent Layer)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐            │
│  │ Supervisor   │  │ Contract     │  │ Document     │            │
│  │ Agent        │  │ Review Agent │  │ Gen Agent    │            │
│  │ (意图路由)   │  │ (合同审查)   │  │ (文书生成)   │            │
│  └──────┬───────┘  └──────────────┘  └──────────────┘            │
│  ┌──────┴───────────────────────────────────────┐                │
│  │  Legal Consult Agent  │  Law Retrieval Agent │                │
│  │  (法律咨询)           │  (法律检索)          │                │
│  └──────────────────────────────────────────────┘                │
├──────────────────────────────────────────────────────────────────┤
│                      数据层 (Data Layer)                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │PostgreSQL│  │  Redis   │  │ ChromaDB │  │  Neo4j   │         │
│  │ (业务库) │  │  (缓存)  │  │ (向量库) │  │ (知识图谱)│         │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
├──────────────────────────────────────────────────────────────────┤
│                     基础设施层 (Infrastructure)                    │
│       Docker Compose  │  Nginx  │  GitHub Actions  │  Prometheus  │
└──────────────────────────────────────────────────────────────────┘
```

### 智能体工作流

```
用户查询 → SupervisorAgent (意图分类)
              │
              ├── legal_consultation → LegalConsultAgent (法律咨询 + RAG)
              ├── contract_review     → ContractReviewAgent (合同解析 + 风险识别)
              ├── document_generation → DocumentGenAgent (要素提取 + 模板匹配)
              ├── law_retrieval       → LawRetrievalAgent (语义搜索 + 关键词搜索)
              └── general             → 通用回复
```

## 快速开始

### 环境要求

- **Python** 3.11+
- **Node.js** 20+
- **PostgreSQL** 16+ (可选，开发模式可使用 SQLite 替代)
- **Redis** 7+ (可选，用于缓存)

### 方式一：Windows 一键启动

```bash
# 双击运行或在命令行执行
start.bat
```

脚本将自动完成：
1. 检查 Python 和 Node.js 环境
2. 安装后端 Python 依赖
3. 安装前端 Node.js 依赖
4. 初始化数据库
5. 加载法律知识种子数据
6. 启动后端和前端服务

### 方式二：Docker Compose 启动

```bash
# 启动所有服务
docker-compose up -d

# 启动开发模式（含 ChromaDB）
docker-compose --profile dev up -d

# 停止所有服务
docker-compose down
```

### 方式三：手动启动

**后端：**

```bash
cd backend

# 创建虚拟环境
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

# 安装依赖
pip install -r requirements.txt

# 复制环境配置
copy .env.example .env
# 编辑 .env 填入你的 API Key 和数据库配置

# 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**前端：**

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

## API 文档

启动后端服务后，访问 http://localhost:8000/docs 查看 Swagger API 文档。

### API 端点概览

| 方法 | 路径 | 描述 |
|------|------|------|
| **认证** | | |
| POST | `/api/v1/auth/register` | 用户注册 |
| POST | `/api/v1/auth/login` | 用户登录 |
| POST | `/api/v1/auth/refresh` | 刷新 Token |
| **聊天** | | |
| POST | `/api/v1/chat/chat` | 发送法律咨询消息 |
| GET | `/api/v1/chat/conversations` | 获取对话列表 |
| GET | `/api/v1/chat/conversations/{id}` | 获取对话详情 |
| **合同审查** | | |
| POST | `/api/v1/contract/review` | 上传并审查合同 |
| GET | `/api/v1/contract/reviews` | 获取审查记录列表 |
| GET | `/api/v1/contract/reviews/{id}` | 获取审查详情 |
| **文书生成** | | |
| POST | `/api/v1/document/generate` | 生成法律文书 |
| GET | `/api/v1/document/templates` | 获取文书模板列表 |
| **法律检索** | | |
| POST | `/api/v1/law/search` | 搜索法律法规 |
| GET | `/api/v1/law/articles` | 获取法条分类列表 |
| GET | `/api/v1/law/categories` | 获取法律类别 |
| **反馈** | | |
| POST | `/api/v1/feedback/submit` | 提交反馈 |
| GET | `/api/v1/feedback/summary` | 获取反馈统计 |
| **分析** | | |
| GET | `/api/v1/analytics/dashboard` | 系统仪表盘统计 |
| GET | `/api/v1/analytics/topics` | 热门咨询话题 |
| GET | `/api/v1/analytics/trends` | 使用趋势分析 |

### 请求示例

**法律咨询：**

```bash
curl -X POST http://localhost:8000/api/v1/chat/chat \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "劳动合同到期不续签，公司需要赔偿吗？",
    "agent_type": "legal_consultation"
  }'
```

**合同审查：**

```bash
curl -X POST http://localhost:8000/api/v1/contract/review \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -F "file=@contract.pdf" \
  -F "review_type=comprehensive"
```

**文书生成：**

```bash
curl -X POST http://localhost:8000/api/v1/document/generate \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "template_type": "complaint_filing",
    "parameters": {
      "原告姓名/名称": "张三",
      "原告住所地": "北京市朝阳区",
      "被告姓名/名称": "李四",
      "被告住所地": "上海市浦东新区",
      "诉讼请求": "要求被告支付货款人民币50万元及逾期利息",
      "案件事实": "2024年1月，原告与被告签订供货合同，原告已按约定交付货物，被告至今未支付货款。",
      "法律依据": "《中华人民共和国民法典》第577条、第579条"
    },
    "language": "zh"
  }'
```

## 技术栈

### 后端

| 技术 | 用途 |
|------|------|
| **FastAPI** | Web 框架，异步 REST API |
| **LangChain** | LLM 应用开发框架 |
| **LangGraph** | 多智能体工作流编排 |
| **SQLAlchemy 2.0** | 异步 ORM，数据库操作 |
| **PostgreSQL** | 主数据库，存储业务数据 |
| **Redis** | 缓存、会话管理、语义缓存 |
| **ChromaDB** | 向量数据库，RAG 检索 |
| **Neo4j** | 知识图谱，法条关系网络 |
| **Pydantic** | 数据验证和序列化 |
| **JWT** | 用户认证和授权 |

### 前端

| 技术 | 用途 |
|------|------|
| **Vue 3** | 前端框架 (Composition API) |
| **TypeScript** | 类型安全的 JavaScript |
| **Vite** | 构建工具 |
| **Element Plus** | UI 组件库 |
| **Pinia** | 状态管理 |
| **Vue Router** | 路由管理 |
| **Axios** | HTTP 客户端 |
| **Markdown-it** | Markdown 渲染 |

### AI / LLM

| 技术 | 用途 |
|------|------|
| **DeepSeek** | 默认 LLM 提供商 |
| **OpenAI** | 备选 LLM 提供商 |
| **Sentence Transformers** | 文本嵌入模型 |
| **RAG** | 检索增强生成，法律知识增强 |

## 项目结构

```
Legal Intelligent Assistance System/
├── backend/                        # 后端 Python 项目
│   ├── app/
│   │   ├── agents/                 # AI 智能体
│   │   │   ├── base_agent.py       # 智能体基类
│   │   │   ├── supervisor_agent.py # 路由智能体 (意图分类)
│   │   │   ├── legal_consult_agent.py   # 法律咨询智能体
│   │   │   ├── contract_review_agent.py # 合同审查智能体
│   │   │   ├── document_gen_agent.py    # 文书生成智能体
│   │   │   └── law_retrieval_agent.py   # 法律检索智能体
│   │   ├── api/                    # API 路由层
│   │   │   ├── deps.py             # 依赖注入
│   │   │   └── v1/
│   │   │       ├── router.py       # 路由聚合
│   │   │       ├── auth.py         # 认证接口
│   │   │       ├── chat.py         # 聊天接口
│   │   │       ├── contract.py     # 合同审查接口
│   │   │       ├── document.py     # 文书生成接口
│   │   │       ├── law.py          # 法律检索接口
│   │   │       ├── analytics.py    # 分析统计接口
│   │   │       └── feedback.py     # 用户反馈接口
│   │   ├── core/                   # 核心配置
│   │   │   ├── config.py           # 应用配置
│   │   │   └── database.py         # 数据库连接
│   │   ├── models/                 # 数据库模型
│   │   │   ├── base.py             # 基础模型
│   │   │   ├── user.py             # 用户模型
│   │   │   ├── conversation.py     # 对话模型
│   │   │   ├── message.py          # 消息模型
│   │   │   ├── document.py         # 文档模型
│   │   │   └── feedback.py         # 反馈模型
│   │   ├── prompts/                # 法律 Prompt 模板
│   │   │   └── legal_prompts.py    # 系统提示词和模板
│   │   ├── rag/                    # RAG 检索增强
│   │   │   ├── document_processor.py  # 文档解析
│   │   │   ├── embedding_service.py   # 嵌入服务
│   │   │   ├── retriever.py           # 检索器
│   │   │   ├── vector_store.py        # 向量存储
│   │   │   ├── knowledge_graph.py     # 知识图谱
│   │   │   └── seed_data.py           # 种子数据
│   │   ├── schemas/                # Pydantic 数据模型
│   │   ├── services/               # 业务服务
│   │   │   ├── llm_service.py      # LLM 服务
│   │   │   ├── feedback_service.py # 反馈服务
│   │   │   └── semantic_cache.py   # 语义缓存
│   │   └── main.py                 # 应用入口
│   ├── Dockerfile                  # 后端 Docker 镜像
│   ├── requirements.txt            # Python 依赖
│   └── .env.example                # 环境变量模板
├── frontend/                       # 前端 Vue3 项目
│   ├── src/
│   │   ├── api/                    # API 请求模块
│   │   ├── components/             # 公共组件
│   │   ├── views/                  # 页面视图
│   │   ├── router/                 # 路由配置
│   │   ├── stores/                 # Pinia 状态管理
│   │   ├── styles/                 # 样式文件
│   │   ├── App.vue                 # 根组件
│   │   └── main.ts                 # 应用入口
│   ├── Dockerfile                  # 前端 Docker 镜像
│   ├── nginx.conf                  # Nginx 配置
│   └── package.json                # Node.js 依赖
├── docs/                           # 文档
│   └── ARCHITECTURE.md             # 架构文档
├── docker-compose.yml              # Docker Compose 编排
├── start.bat                       # Windows 启动脚本
└── README.md                       # 项目说明
```

## 开发指南

### 环境配置

1. 复制 `backend/.env.example` 为 `backend/.env`
2. 配置 LLM API Key（支持 DeepSeek 和 OpenAI）
3. 配置数据库连接信息
4. 配置 Redis 连接信息（可选）

### 关键配置项

```env
# LLM 配置
LLM_PROVIDER=deepseek           # deepseek 或 openai
DEEPSEEK_API_KEY=sk-xxx         # DeepSeek API Key
DEEPSEEK_MODEL=deepseek-chat    # 模型名称

# 数据库
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/legal_assistant

# JWT
JWT_SECRET_KEY=your-secret-key  # 至少 32 字符
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
```

### 添加新的智能体

1. 在 `app/agents/` 中创建新的 Agent 类，继承 `BaseAgent`
2. 实现 `_build_graph()` 定义 LangGraph 工作流
3. 实现 `run()` 方法
4. 在 `SupervisorAgent` 中注册新的路由规则
5. 创建对应的 API 端点

### 运行测试

```bash
cd backend
pytest
```

### 代码规范

- Python: 遵循 PEP 8，使用 type hints
- TypeScript: 使用 strict 模式
- 提交信息: 遵循 Conventional Commits

## 许可证

本项目仅供学习和研究使用。法律建议仅供参考，不构成正式法律意见。

---

## 法律知识库扩充

本项目包含完整的法律知识库扩充工具链，目标是将知识库从当前的 143 条扩充到 **1 亿+** 条法律条文/案例。

### 当前数据统计

| 数据类型 | 当前数量 | 目标数量 |
|---------|---------|---------|
| 法律 | 31 部 | 1000+ 部 |
| 法条 | 112 条 | 1000 万+ 条 |
| 案例 | 10 个 | 1 亿+ 个 |
| 法律概念 | 18 个 | 10 万+ 个 |

### 数据源

- **裁判文书网** (wenshu.court.gov.cn) - 1.3 亿+ 裁判文书 ✅ 已完成爬虫
- **法律法规信息网** (pkulaw.com) - 100 万+ 法律法规 ⏳ 待实现
- **国家知识产权局** (cnipa.gov.cn) - 5000 万+ 专利 ⏳ 待实现
- **市场监管行政处罚** - 1000 万+ 处罚案例 ⏳ 待实现

### 快速开始

```bash
# 1. 测试爬虫功能
cd backend
python scripts/test_crawler.py

# 2. 启动爬虫（单次爬取 10 页）
python scripts/start_crawler.py --source wenshu --pages 10

# 3. 处理爬取的数据
python scripts/start_crawler.py --process ./crawler_data

# 4. 导入到数据库
python scripts/start_crawler.py --import ./crawler_data_processed
```

### 相关文档

- [爬虫使用指南](docs/CRAWLER_GUIDE.md)
- [数据扩充计划](docs/DATA_EXPANSION_PLAN.md)
- [知识库扩充总结](docs/KNOWLEDGE_BASE_SUMMARY.md)
- [Milvus 优化方案](backend/app/rag/milvus_optimization.py)

### 技术架构

```
数据爬取 → 数据清洗 → 结构化处理 → 向量嵌入 → 批量入库
   │           │           │            │           │
   ↓           ↓           ↓            ↓           ↓
裁判文书网   去HTML     提取字段     BGE-M3     PostgreSQL
法律法规网   去特殊符   分词标签     1024维     Milvus
行政处罚网   规范化     关键词       嵌入模型    向量数据库
```

---

**法律声明**：本系统由 AI 生成的法律内容仅供参考，不构成正式法律意见。对于涉及重大权益的法律问题，请务必咨询持证执业律师。