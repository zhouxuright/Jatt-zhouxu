# 法律智能助手系统 (Legal Intelligent Assistance System)

基于 AI 大模型的中国法律智能助手平台，提供法律咨询、合同审查、文书生成、
法规与案例检索、诉讼支持、企业合规等智能服务。

> **文档状态**：本次修订于 2026-09-24，所有数字均为**直连生产库实测**，非设计目标。
> 上一版（2026-08-15）中的数据规模与功能清单已严重滞后，请以本版为准。

## 项目概述

系统利用 LangGraph/LangChain 构建多智能体协作架构，通过 RAG（检索增强生成）
提供法律知识服务，并针对法律实务场景做了三项针对性设计：

- **引用可溯源**：逐条给出置信度，经引用核验闸门后才进入回答
- **数据不出内网**：Docker Compose 单机私有化部署，字段级加密 + 多租户隔离
- **AIGC 合规**：生成内容带水印与合规过滤

### 核心功能

| 功能模块 | 描述 |
|---------|------|
| **智能对话** | 多智能体路由，支持普通问答与深度思考两种模式 |
| **深度推理** | 可视化思维链：IRAC 四段论证、多步推理、置信度仪表盘 |
| **技能包** | 19 个专业技能包（劳动争议、合同分析、知识产权等），多步骤编排 |
| **合同审查** | 上传合同（PDF/DOCX/DOC/TXT），识别违约、知识产权、保密等 8 类风险 |
| **合同全生命周期** | 起草 / 审查 / 比对 / 归档等功能区 |
| **文书生成** | 诉状、答辩状、律师函、法律意见书、仲裁申请书等多类模板 |
| **法规检索** | 语义 + 关键词多策略检索，覆盖宪法、刑法、民法典等各大部门法 |
| **案例检索** | 按案由检索，支持民事等大类超集匹配 |
| **诉讼支持** | 证据清单、庭审准备、类案比对、裁判预测 |
| **合规管理** | 法规动态跟踪、自动风险排查 |
| **批量处理** | 多文档批量上传与处理 |
| **数据资产治理** | 语料登记、许可状态、覆盖度统计 |
| **多智能体协同** | LangGraph 协作图，Supervisor 路由 + 多节点并行 |
| **工具调用** | 内置工具注册表（MCP 风格 REST 暴露），**管理员调试页** |
| **数据飞轮** | 用户反馈分析、使用趋势统计、热门话题追踪 |

## 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        用户层 (User Layer)                        │
│           Vue3 SPA  │  Nginx  │  REST API  │  SSE 流式           │
├──────────────────────────────────────────────────────────────────┤
│                        API 网关 (FastAPI)                         │
│   认证 (JWT)  │  限流  │  CORS  │  文件上传  │  审计  │ 异常处理  │
├──────────────────────────────────────────────────────────────────┤
│                    智能体层 (Agent Layer) — 14 个智能体            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐            │
│  │ Supervisor   │  │ Contract     │  │ Document     │            │
│  │ Agent        │  │ Review Agent │  │ Gen Agent    │            │
│  │ (意图路由)   │  │ (合同审查)   │  │ (文书生成)   │            │
│  └──────┬───────┘  └──────────────┘  └──────────────┘            │
│  ┌──────┴──────────────────────────────────────────────────┐     │
│  │ 法律咨询 │ 法规检索 │ 深度推理 │ 诉讼支持 │ 合规风控     │     │
│  │ 合同履历 │ 协作编排 │ 研究 │ 复核 │ 口语化改写          │     │
│  └──────────────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────────────┤
│                    检索与治理层 (RAG Layer)                        │
│  引用核验闸门 │ AIGC 水印 │ 内容安全过滤 │ 逐条置信度             │
├──────────────────────────────────────────────────────────────────┤
│                      数据层 (Data Layer)                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │PostgreSQL│ │  Redis   │ │  Milvus  │ │  Neo4j   │            │
│  │ (业务库) │ │(缓存/会话)│ │(向量检索)│ │(知识图谱)│            │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘            │
│  ┌──────────┐ ┌──────────┐                                       │
│  │Elastic   │ │  MinIO   │  （Milvus 依赖 etcd + MinIO）         │
│  │search    │ │(对象存储)│                                       │
│  └──────────┘ └──────────┘                                       │
├──────────────────────────────────────────────────────────────────┤
│                     基础设施层 (Infrastructure)                    │
│       Docker Compose  │  Nginx  │  Prometheus  │  Grafana        │
└──────────────────────────────────────────────────────────────────┘
```

> **架构说明**：向量检索以 **Milvus** 为主（`vector_store.py` 自动探测，
> 探测不到时回退 ChromaDB 供本地开发）。

### 智能体工作流

```
用户查询 → SupervisorAgent (意图分类)
              │
              ├── legal_consultation   → LegalConsultAgent    (法律咨询 + RAG)
              ├── contract_review      → ContractReviewAgent  (合同解析 + 风险识别)
              ├── document_generation  → DocumentGenAgent     (要素提取 + 模板匹配)
              ├── law_retrieval        → LawRetrievalAgent    (语义 + 关键词检索)
              └── general              → 通用回复
```

## 数据规模（2026-09-24 直连数据库实测）

### PostgreSQL

| 表 | 内容 | 行数 | 可否作为法律依据引用 |
| --- | --- | ---: | --- |
| `legal_articles` | 法条 | 686,906 | ✅ 权威 |
| `laws` | 法规 | 18,580 | ✅ 权威 |
| `judicial_interpretations` | 司法解释 | 219 | ✅ 权威 |
| `legal_cross_references` | 法条交叉引用 | 20,219 | ✅ 可商用 |
| `court_cases` | 案例 | 1,210,726 | ⚠️ **商用许可未核验** |
| `legal_qa_pairs` | 问答对 | 873,678 | ❌ 不引用（仅训练/评测） |
| `legal_concepts` | 法律概念 | 18 | ⚠️ 覆盖不足 |

**可引用语料合计约 72.6 万条**（法条 + 法规 + 司法解释 + 交叉引用）。

### Milvus（向量库）

| 集合 | 向量数 |
| --- | ---: |
| `legal_articles` | 693,536 |
| `legal_cases` | 307,780 |
| `user_documents` | 4（企业知识库，待建设） |
| `legal_knowledge` | 0（未启用） |

### 关于数据规模的诚实说明

**对外指标不应是"有多少条"，而应是"引用是否可溯源、是否准确、是否有商用许可"。**

- `court_cases` 的 121 万条中，相当部分为 HuggingFace 数据集的**标签级记录**
  （`case_number` 形如 `HF-97de3926bbfd5054`，`court_name` 为空），
  **不是可直接展示的裁判文书**，商用许可需逐源复核。
- `corpus_registry` 表已提供 `citable` 机制用于标记可引用性；
  凡未核验许可的数据**不得作为法律依据展示**。
- 仓库**不含**任何大规模数据集；`backend/data/` 为运行时输入目录，已在
  `.gitignore` 中排除。

## 快速开始

### 环境要求

- **Python** 3.11+
- **Node.js** 20+
- **PostgreSQL** 16+（Docker 方式已内含）
- **Docker** 与 **Docker Compose**（推荐）

### 方式一：Docker Compose（推荐）

```bash
# 启动核心服务（backend 双副本 + frontend + postgres + redis + milvus 等）
docker compose up -d

# 含生产反向代理（Nginx 负载均衡）
docker compose --profile prod up -d

# 启动全部服务
docker compose --profile full up -d

# 停止
docker compose down
```

> ⚠️ 可用 profile 只有 **`prod`** 与 **`full`**（旧文档中的 `--profile dev` 并不存在）。

**服务端口（实测）**

| 服务 | 宿主机端口 |
| --- | --- |
| 前端 | `3000` |
| Nginx（prod profile） | `80` |
| PostgreSQL | **`5433`** |
| Redis | `6379` |
| Milvus | `19530` |
| Neo4j | `7474` / `7687` |
| Elasticsearch | `9200` |

> 后端容器**不对外发布端口**，仅在 `legal_network` 内以 `8000` 提供服务；
> 外部访问经前端 / Nginx 转发。PostgreSQL 宿主机端口是 **5433**（不是 5432）。

### 方式二：Windows 一键启动

```bash
start.bat
```

### 方式三：手动启动（开发）

**后端：**

```bash
cd backend

python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate        # Linux/Mac

pip install -r requirements.txt

copy .env.example .env          # 然后填入 API Key 与数据库配置

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**前端：**

```bash
cd frontend
npm install
npm run dev
```

## API 文档

启动后端后访问 **http://localhost:8000/docs** 查看完整 Swagger 文档。

系统共 **153 个端点**、**23 个子路由**。主要路由前缀：

| 前缀 | 功能 |
| --- | --- |
| `/api/v1/auth` | 认证（注册 / 登录 / 刷新 / 当前用户） |
| `/api/v1/chat` | 智能对话（含 SSE 流式、上传、追问、摘要） |
| `/api/v1/contract` | 合同审查 + 合同全生命周期 |
| `/api/v1/document` | 文书生成与模板 |
| `/api/v1/law` | 法规检索 |
| `/api/v1/cases` | 案例检索 |
| `/api/v1/reasoning` | 深度推理 |
| `/api/v1/litigation` | 诉讼支持 |
| `/api/v1/compliance` | 合规风控 |
| `/api/v1/skills` | 技能包 |
| `/api/v1/mcp` | 工具注册表（**执行端点仅管理员**） |
| `/api/v1/knowledge` | 知识图谱 |
| `/api/v1/collaboration` | 多智能体协同 |
| `/api/v1/analytics` | 分析统计 |
| `/api/v1/data` | 数据资产治理 |
| `/api/v1/admin` | 管理后台 |
| `/api/v1/batch` | 批量处理 |

> **鉴权**：除认证接口与健康检查外，所有路由默认要求 JWT。
> 该保护在 `api/v1/router.py` **统一挂载**，新增路由默认受保护，
> 而非依赖逐个记得添加。

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

**流式对话：** `POST /api/v1/chat/chat/stream`（SSE，支持 `model_override` 与
深度思考的 `depth` 参数）

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
| **LangGraph / LangChain** | 多智能体工作流编排 |
| **SQLAlchemy 2.0** | 异步 ORM |
| **PostgreSQL** | 主数据库 |
| **Redis** | 缓存、会话、语义缓存 |
| **Milvus** | 向量检索（生产） |
| **ChromaDB** | 向量检索（本地开发回退） |
| **Neo4j** | 知识图谱 |
| **Elasticsearch** | 全文检索 |
| **Pydantic** | 数据验证与序列化 |
| **JWT + passlib/bcrypt** | 认证与口令哈希 |
| **Alembic** | 数据库迁移 |
| **Prometheus** | 指标采集 |

### 前端

| 技术 | 用途 |
|------|------|
| **Vue 3** | 前端框架（Composition API + `<script setup>`） |
| **TypeScript** | 类型安全 |
| **Vite** | 构建工具 |
| **Element Plus** | UI 组件库 |
| **Pinia** | 状态管理 |
| **Vue Router** | 路由（含 `requiresAuth` / `requiresAdmin` 守卫） |
| **Axios** | HTTP 客户端 |
| **Markdown-it** | Markdown 渲染 |

### AI / 模型

| 项 | 说明 |
|------|------|
| **嵌入模型** | `BAAI/bge-m3` |
| **重排模型** | `BAAI/bge-reranker-v2-m3` |
| **LLM 提供商** | DeepSeek（默认）、OpenAI，并支持 Qwen / GLM 等 |
| **RAG** | 多策略召回 + 引用核验 + 置信度 |
| **可解释性** | IRAC 推理框架、思维链可视化 |

### 安全与合规

| 能力 | 实现位置 |
|------|------|
| **字段级加密 + 盲索引** | `app/core/crypto.py`、`app/services/encryption.py` |
| **多租户隔离** | `app/core/tenancy.py` |
| **AIGC 水印** | `app/services/content_watermark.py` |
| **AIGC 合规过滤** | `app/services/aigc_compliance.py` |
| **引用核验闸门** | `app/services/citation_verifier.py` |
| **审计日志** | `app/models/audit_log.py` + `/api/v1/admin` |

## 项目结构

```
Legal Intelligent Assistance System/
├── backend/
│   ├── app/
│   │   ├── agents/                 # 14 个智能体
│   │   │   ├── base_agent.py               # 基类
│   │   │   ├── supervisor_agent.py         # 意图路由
│   │   │   ├── legal_consult_agent.py      # 法律咨询
│   │   │   ├── contract_review_agent.py    # 合同审查
│   │   │   ├── contract_lifecycle_agent.py # 合同全生命周期
│   │   │   ├── document_gen_agent.py       # 文书生成
│   │   │   ├── law_retrieval_agent.py      # 法规检索
│   │   │   ├── deep_thinking_agent.py      # 深度推理（IRAC）
│   │   │   ├── litigation_support_agent.py # 诉讼支持
│   │   │   ├── compliance_risk_agent.py    # 合规风控
│   │   │   ├── collaboration.py            # 多智能体协同
│   │   │   ├── colloquial_rewrite_agent.py # 口语化改写
│   │   │   ├── research_agent.py           # 研究
│   │   │   └── review_agent.py             # 复核
│   │   ├── api/
│   │   │   ├── deps.py             # 依赖注入（含 get_current_admin_user）
│   │   │   └── v1/                 # 23 个子路由
│   │   │       ├── router.py       # 路由聚合 + 统一鉴权
│   │   │       └── ...             # auth / chat / contract / document /
│   │   │                           # law / cases / reasoning / litigation /
│   │   │                           # compliance / admin / mcp_tools 等
│   │   ├── core/                   # 配置、安全、加密、租户
│   │   │   ├── config.py           # 应用配置
│   │   │   ├── crypto.py           # 字段级加密
│   │   │   ├── tenancy.py          # 多租户
│   │   │   └── security.py         # 安全工具
│   │   ├── models/                 # 数据模型（含 audit_log）
│   │   ├── mcp/                    # 工具注册表（自研，非官方 MCP SDK）
│   │   ├── skills/                 # 19 个技能包
│   │   ├── rag/                    # 检索增强
│   │   │   ├── advanced_retriever.py   # 主检索器
│   │   │   ├── document_processor.py   # 文档解析（含 OLE2 .doc）
│   │   │   ├── embedding_service.py    # bge-m3
│   │   │   ├── vector_store.py         # Milvus / ChromaDB
│   │   │   ├── knowledge_graph.py      # Neo4j
│   │   │   └── mass_import/            # 批量导入流水线
│   │   ├── services/               # 业务服务
│   │   │   ├── tool_orchestrator.py    # 工具编排
│   │   │   ├── citation_verifier.py    # 引用核验
│   │   │   ├── content_watermark.py    # AIGC 水印
│   │   │   ├── encryption.py           # 加密服务
│   │   │   └── ...
│   │   └── main.py                 # 应用入口
│   ├── alembic/                    # 数据库迁移
│   ├── tests/                      # 测试套件
│   ├── scripts/                    # 运维 / 数据脚本
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example                # 环境变量模板
├── frontend/
│   ├── src/
│   │   ├── api/                    # API 请求模块
│   │   ├── components/             # 公共组件（AppLayout / ChatMessage 等）
│   │   ├── views/                  # 18 个页面
│   │   ├── router/                 # 路由 + 权限守卫
│   │   ├── stores/                 # Pinia（含 auth 的 isAdmin）
│   │   └── styles/
│   ├── Dockerfile
│   ├── nginx.conf                  # 含安全响应头
│   └── package.json
├── docs/                           # 项目文档
├── monitoring/                     # Prometheus + Grafana
├── nginx/                          # 生产反向代理
├── scripts/                        # 备份等运维脚本
├── docker-compose.yml
├── docker-compose.prod.yml
├── start.bat
├── .gitignore
└── README.md
```

## 开发指南

### 环境配置

1. 复制 `backend/.env.example` 为 `backend/.env`
2. 配置 LLM API Key（默认 DeepSeek，可切 OpenAI / Qwen / GLM）
3. 配置数据库连接（注意 Docker 下宿主机端口为 **5433**）

> ⚠️ `.env` **绝不可提交**。本仓库为公开仓库，且 `.env` 内含真实 API Key
> 与 JWT secret（已在 `.gitignore` 中排除）。

### 关键配置项

```env
# LLM
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_MODEL=deepseek-chat

# 数据库（Docker 部署时宿主机端口为 5433）
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/legal_assistant

# JWT
JWT_SECRET_KEY=your-secret-key      # 至少 32 字符
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
```

### 添加新的智能体

1. 在 `app/agents/` 创建 Agent 类，继承 `BaseAgent`
2. 实现 `_build_graph()` 定义 LangGraph 工作流
3. 实现 `run()` 方法
4. 在 `SupervisorAgent` 注册路由规则
5. 新建对应 API 端点（记得到 `api/v1/router.py` 挂载）

### 运行测试

```bash
cd backend
pytest
```

> 测试在宿主机运行（容器镜像内不含 `tests/`）。

### 代码规范

- Python：遵循 PEP 8，使用 type hints
- TypeScript：strict 模式
- 提交信息：遵循 Conventional Commits

## 数据源与合规

系统使用的数据按**可引用性**分为三类，切勿混用：

| 类别 | 来源 | 用途 |
| --- | --- | --- |
| **权威可引用** | 国家法律法规数据库（flk.npc.gov.cn）、最高法指导性案例与公报案例 | 作为法律依据展示 |
| **研究许可** | CAIL / LAIC 等学术数据集、开源案例数据集 | 仅用于**评测与微调**，不作引用 |
| **需商业授权** | 北大法宝 / 威科先行 / 中国司法大数据研究院等 | 商用前须取得授权 |

### ⚠️ 关于爬取裁判文书网

- 裁判文书网（wenshu.court.gov.cn）**自 2021 年起已限制批量获取**，
  以爬取方式获取数据用于**商业用途存在法律风险**。
- 仓库中保留的爬虫代码仅作技术研究，**不应**用于商业数据获取。
- 任何案例数据在确认商用许可前，**不得**作为法律依据展示给用户。

## 许可证与免责声明

本项目仅供学习和研究使用。

**免责声明**：本系统由 AI 生成的法律内容仅供参考，**不构成正式法律意见**，
亦不能替代执业律师的专业判断。对于涉及重大权益的法律问题，请务必咨询持证执业律师。
系统输出的法条、案例引用请以官方权威来源为准。

---

**法律声明**：本系统由 AI 生成的法律内容仅供参考，不构成正式法律意见。
