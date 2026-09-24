# Demo → 商业化落地 差距分析报告

**日期**：2026-09-24
**方法**：全量代码盘点 + 运行中系统实测（Docker 双副本）+ 联网行业调研
**结论**：工程完成度约 **65%**，但商业化落地约 **30%**。工程不是主要瓶颈；
**数据许可、版本管理、评测闭环** 三项才是真正的路障。

---

## 一、系统当前的真实状态（实测，非文档声称）

### 1.1 运行栈

```
legalintelligentassistancesystem-backend-5 / -6   双副本 healthy
legal_frontend        :3000    legal_nginx       :80
legal_postgres        :5433    legal_redis       :6379
legal_milvus          :19530   legal_neo4j       :7474/7687
legal_elasticsearch   :9200    legal_minio       9000
legal_etcd / proxy_pool :5010
```

FastAPI **149 个端点**（22 个子路由）、**23 张表**、**9 个 Alembic 迁移**、
**146 个测试通过**（本次新增 23 个）。

> ⚠️ **测试结论的可复现性存疑（同日复核）**：这个"146 通过"是在**宿主机**跑的 ——
> 容器内**没有 `tests/` 目录**，`pytest tests` 直接报 `file or directory not found`。
> 且在宿主机上全量套件**不稳定**：连续三次运行分别为 `137 passed/51 errors`、
> `172/16`、`120/68`。根因是 `OSError: [WinError 10055]`（Windows 套接字资源耗尽），
> 栈在 `asyncio.Runner.__enter__` → `socket.socketpair()`，属**环境问题而非用例失败**：
> 与本次改动完全无关的 `tests/test_feedback.py`、`test_content_safety.py` 单独跑
> 同样报此错；而 `socketpair()` 直接调用正常、TIME_WAIT 仅约 100，说明是临界点波动。
> 可疑诱因：`tests/conftest.py` 里 session 级 `event_loop` fixture 是
> pytest-asyncio **0.x** 的写法（注释仍写着 "required for pytest-asyncio on Windows"），
> 而实际安装的是 **1.4.0**。**在接 CI（§7-P1-7）之前必须先修掉这个**，
> 否则评测闭环建立在不可复现的测试上。

embedding / rerank 模型实际加载：`BAAI/bge-m3` + `BAAI/bge-reranker-v2-m3`（`/health` 自证）。

### 1.2 数据量（2026-09-24 直连数据库实测）

| 存储 | 表 / 集合 | 行数 | 可否作为法律依据引用 |
| --- | --- | ---: | --- |
| PostgreSQL | `legal_articles`（法条） | 686,906 | ✅ 权威 |
| PostgreSQL | `laws`（法规） | 18,580 | ✅ 权威 |
| PostgreSQL | `court_cases`（案例） | 1,210,726 | ⚠️ **许可未核验** |
| PostgreSQL | `legal_qa_pairs` | 873,678 | ❌ 不引用 |
| PostgreSQL | `legal_knowledge_entries` | 194,339 | ❌ 不引用 |
| PostgreSQL | `legal_cross_references` | 20,219 | ✅ 可商用 |
| PostgreSQL | `legal_qa`（SFT） | 19,332 | ❌ 仅训练 |
| PostgreSQL | `judicial_interpretations` | **0** | — |
| PostgreSQL | `legal_concepts` | 18 | 覆盖不足 |
| **PG 合计** | | **≈ 2,984,229** | |
| Milvus | `legal_articles` | 693,536 | ✅ |
| Milvus | `legal_cases` | 307,780 | ⚠️ |
| Milvus | `user_documents` | 4 | 企业知识库 |
| **Milvus 合计** | | **≈ 1,001,320** | |

### 1.3 磁盘上的 1500 万条"合成数据"——**不是资产，是负债**

```
backend/data/mass_expansion/synthetic_qa_pairs.jsonl   10,000,000 条   12 GB
backend/data/mass_expansion/synthetic_cases.jsonl       5,000,000 条  3.4 GB
```

`corpus_registry` 表已把它们正确标记为 `provenance='synthetic'`、**`citable=false`**、
`commercial_use='⛔ 严禁入库到检索语料或作为法律依据展示'`。

抽样实证（`synthetic_cases.jsonl` 第 1 条）：

```json
{"case_number": "(2018)市鼓民初第76972号", "court": "南京市鼓楼区人民法院",
 "judgment": "法院经审理认为，根据相关法律法规，无固定期限问题应依法处理。"}
```

**案号是编造的，法院是编造的，判决理由是空话。** 这 1500 万条：

- ❌ 未进 PostgreSQL
- ❌ 未进 Milvus
- ❌ 不可引用
- ⚠️ 占 16 GB 磁盘，且**极易被误当作真实语料**

`backend/data/billion_gen_progress.json` 记录"亿级生成器"实跑结果为
`total_generated: 0`，21 秒后退出 —— **"上亿数据"目标从未达成**。

### 1.4 真实可引用语料合计：**约 298 万条**，不是 1 亿，也不是 1500 万

**"上亿"作为一个营销数字与"可检索、可引用、有许可"的数据量是两件事**，
混淆二者在法律行业是高风险行为（见 §4.3）。

---

## 二、本次已修复的缺陷（全部实测验证）

### 2.1 正确性缺陷

| # | 缺陷 | 症状 | 修复 |
| --- | --- | --- | --- |
| 1 | `government_regulation` 工具查错表 + 查不存在的列 | 每次调用 `UndefinedColumnError: column "abstract" does not exist`；且 `judicial_interpretations` 表 0 行 | 改查 `laws`（339 条司法解释在此）；实测返回《劳动争议解释（一）（二）》 |
| 2 | `wenshu_search` 的 `case_type=民事` 恒返回 0 | 案例库把劳动争议/婚姻家庭/知识产权各列为独立 `case_type`，精确匹配 `民事` 把最常用的民事子类全部隐藏 | `民事` 视为超集；同一查询由 0 → 32 条 |
| 3 | 旧版 `.doc` 上传必失败 | `.doc` 被路由给 python-docx，OLE2 二进制 → `PackageNotFoundError`，`parse_success:false` | 新增真正的 OLE2 `WordDocument` 流解析；无法解析时报可操作错误而非返回垃圾 |
| 4 | 兜底解码把二进制变成"正文" | 2 KB 垃圾文件解码出 101 个字符并被当作文档内容 → 会被索引进法律语料 | 增加 `_looks_like_document_text` 比例闸门；排除 `gb18030`（任何字节对都能映射成合法汉字，随机二进制得分 92%） |
| 5 | `deep_think_steps` 算了却从不显示 | 后端每轮深度思考计算 IRAC 步骤并放进 SSE `meta`，store 也存了，但**没有任何组件渲染** → 点"深度思考"和普通回答看起来一样 | `ChatMessage.vue` 新增推理过程面板 |
| 6 | DeepThink 的 `model_override` 在流式路径被丢弃 | 模型下拉只在流式失败的非流式兜底里生效 | `deepThinkStream` 透传 `model_override` |
| 7 | DeepThink 的"分析深度"下拉是纯装饰 | `analysisDepth` 只用于界面显示，后端 agent 根本没有 depth 参数 | 新增 `_depth_policy`：标准=2子问题不验证 / 深度=4+验证 / 专家=8+验证；贯通 API 与前端 |

### 2.2 安全缺陷

| # | 缺陷 | 实测证据 | 修复 |
| --- | --- | --- | --- |
| 8 | **11 个路由零鉴权** | 无 token 调用 `/api/v1/mcp/tools`、`/skills`、`/data/stats`、`/reasoning/*`、`/search/web`、`/litigation/*`、`/compliance/*`、`/contract/lifecycle/*`、`/enterprise/*` **全部返回 200** | 在 `router.py` 统一挂 `Depends(get_current_user)`（默认保护，而非逐个记得） |
| 9 | nginx 安全头**全部丢失** | `add_header` 在嵌套 location 中会**完全丢弃**从 server 块继承的全部 add_header。`/assets/` 与 `/index.html` 各有一个 `add_header Cache-Control`，导致每个 JS/CSS/HTML 响应都没有 `X-Frame-Options / nosniff / X-XSS-Protection / Referrer-Policy / Permissions-Policy` | 改用 `expires` 指令（不触发该行为）；实测 6 个头全部回来了 |
| 10 | `Permissions-Policy: microphone=()` **禁用麦克风** | 语音输入用浏览器 Web Speech API，生产环境被响应头直接禁掉 → 语音按钮永远不可能工作 | 改 `microphone=(self)` |
| 12 | **工具执行端点只有前端守卫（越权）** | 第 8 项的匿名洞堵上后，普通律师（`role=user`）**带合法 token** 仍可直接 `POST /api/v1/mcp/tools/execute`，拿到 `enterprise_lookup` 的 `source="demo_mode"` 编造企业信息 —— 即 §6.1 声称已解决的"律师不应看到"问题实际只关了界面 | `execute` / `execute-batch` 加 `get_current_admin_user`；发现类端点保持开放（对话页 popover 依赖），详见 §6.1 |

### 2.3 数据治理缺陷

| # | 缺陷 | 修复 |
| --- | --- | --- |
| 11 | `/api/v1/data/stats` 返回**假状态** | 原实现累计进程内导入事件，重启即归零，恒报 `imported: 0`、`coverage: 0%`，与库里 298 万条自相矛盾 | 改为直接测量语料表；现返回 `total_records_in_db: 2984229`、`coverage_percent: 2.64` |

> **部署提示**：后端应用代码**烘焙进镜像**，`/app` 无 bind mount。
> 本次改动已同步到两个副本并重启验证（8/8 请求全部 401）。
> 正式生效仍需 `docker compose build backend && docker compose up -d --force-recreate --no-deps backend`，
> 否则下次重建镜像会丢失。
>
> ⚠️ **同日复核更正**：原文称"frontend 已通过 `docker compose build frontend` 正式重建"，
> 但实测 **`legal_frontend` 容器启动时间（07:01 UTC）早于 `dist/` 的构建时间（15:00 本地）**，
> 即容器跑的是**构建前**的镜像 —— 前端同样需要重建。详见 §7-P0-5。

---

## 三、代码盘点的其余发现（未修改，需决策）

### 3.1 "MCP" 不是 MCP

| 事实 | 证据 |
| --- | --- |
| 容器内**没有 `mcp` / `fastmcp` 包** | `import mcp` → `ModuleNotFoundError` |
| 全仓库**没有任何 MCP 传输层** | `grep StdioServerParameters\|stdio_client\|sse_client\|ClientSession` → 0 命中 |
| 自述"without the ``mcp`` Python package" | `app/mcp/server.py:1-11` docstring |
| 协议字符串 `"mcp-2026"` 是硬编码常量 | `app/mcp/server.py:87` |

**现状是"内置工具注册表 + 两套自定义 REST 暴露"**。前端"工具调用"能跑，但
**无法连接任何外部 MCP Server**（Claude Desktop、iManage、Westlaw 等生态接不进来）。
若产品宣传"支持 MCP"，当前实现不成立。

### 3.2 会返回**编造数据**的工具

`enterprise_lookup`（`app/mcp/__init__.py:426`）：`ENTERPRISE_API_BASE/KEY` 为空时返回

```json
{"note": "企业工商查询API尚未配置", "source": "demo_mode"}
```

前端"工具调用"页会把它当正常结果渲染。**律师看到的将是编造的企业信息** —— 这在法律场景是产品责任级别的问题。

### 3.3 已写好但当前部署下不可用的能力

| 能力 | 状态 |
| --- | --- |
| Neo4j 知识图谱检索 | 服务在跑，但认证失败（`Invalid credential`）→ 不可用 |
| Elasticsearch 全文检索 | 服务在跑，**0 个索引**，从未使用 |
| Cross-Encoder rerank | 代码在 CPU 部署下**主动跳过**（`advanced_retriever.py:202-213`，避免打爆 120s 超时），但模型仍加载占 ~4 GB 内存 |
| 19 个技能 | `/skills/stats` 显示 `total_executions: 0` —— **从未被真实调用过** |
| 模型微调（LoRA/QLoRA） | 仅脚手架，无真实训练（`finetuning.py` 自述需 GPU 独立作业） |
| RLS 二线防线 | 脚本已备（`enable_rls_hardening.sql`），未启用 |
| CAIL 2018/2019/2021 | **目录为空，零条数据**；`/data/import/cail` 必然返回 `success: false` |
| 多租户唯一约束 | `username`/`email_bidx` 唯一性是全局的，多租户下"不同租户同名"需改约束 |

### 3.4 工程债

| 项 | 现状 |
| --- | --- |
| **版本管理** | ⚠️ **本项目完全不在 git 中**！仓库根是 `D:/`，追踪的是另一个项目（招投标系统）。本项目 **0 个文件被追踪** → 无回滚、无协作、无 CI 的可能 |
| CI/CD | 无 `.github/workflows` |
| 前端包体 | 主 chunk 1.25 MB（gzip 407 KB），无代码分割 |
| 死代码 | `api/commercial.ts`（170 行）零 import；`searchApi`/`dataApi`/`chatApi.sendChat` 无人调用 |
| 遗留表 | `legal_qa`（19,332 行）不在 ORM 模型中；`legal_knowledge` 表 0 行 |
| 过期注释 | `chat.py:392` 注释里的 embedding/reranker 名与实际（bge-m3 / bge-reranker-v2-m3）不符 |

---

## 四、商业化差距分析

### 4.1 对照"从能回答问题 → 能办成事"

| 能力域 | 需求 | 现状 | 判定 |
| --- | --- | --- | --- |
| 法律问答 | 法条推荐、案例检索 | 已实现且**实测可用**（返回真实法条+出处） | ✅ 75% |
| 文书生成 | 诉状、合同审查、法律意见书 | 89 个模板，`document.py` 9 端点 | ✅ 80% |
| 合同全生命周期 | 起草/审查/比对/归档 | `ContractLifecycle.vue` 5 tab + `lifecycle.py` 11 端点 | ✅ 75% |
| 诉讼支持 | 证据清单/庭审准备/裁判预测 | `LitigationSupport.vue` 6 tab；`/litigation/predict` 后端有、前端未接 | ✅ 70% |
| 合规风险 | 法规动态跟踪/自动排查 | `ComplianceRisk.vue` 5 tab、13 端点；FLK WAF + OCR 验证码破解已实现 | ✅ 70% |
| 多智能体协同 | 像专业团队分工 | LangGraph 真实多智能体，15 个 agent，Supervisor 路由 + 8 节点协作图 | ✅ **80%（完成度最高）** |
| 引用可溯源 | 防幻觉、可验证 | 逐条置信度 + 引用闸门 + 原文溯源接口 | ✅ 75% |
| 数据安全 | 加密、多租户、审计 | 字段级 Fernet 加密 + 盲索引、租户隔离、审计导出 | ✅ 70% |
| 私有化部署 | 数据不出内网 | Docker Compose 单机；无 k8s、无气隙方案 | ⚠️ 40% |
| 个性化定制 | 企业知识库 + 业务流程 | `user_documents` 向量集合**仅 4 条** | ⚠️ 30% |
| 模型自研/微调 | 领域微调 | 仅脚手架 | ❌ 10% |
| 评测闭环 | 幻觉率/引用准确率可量化 | 有 50 题数据集（`hallucination_eval_dataset.json`）+ `run_hallucination_eval.py`，**但未接入 CI，无基准数字** | ❌ 15% |
| 计费/计量 | 按席位/用量 | 无 | ❌ 0% |
| 版本管理与交付 | 可回滚、可审计 | **项目不在 git 中** | ❌ 0% |

### 4.2 行业基准（2026-09 调研）

**架构共识**（Anthropic Claude for Legal / Google Gemini Enterprise for Legal / 智合AI 3.0 / IBM×Shorthills）：

1. **MCP 连接器是生态入口**：Anthropic 12 插件 + 90+ 智能体 + ~20 MCP 连接器，接 Slack / DocuSign / iManage / Westlaw；Google 四层架构同样以"安全 MCP 连接器"为第一层。
   → 本项目**无真实 MCP**，这是与主流产品最大的架构代差。
2. **检索决定上限**：Legal RAG Bench（Isaacus, 2026-03）结论 —— "强检索可弥补弱推理，强推理无法弥补差检索"；**许多被归因于幻觉的错误实际由检索失败触发**。
3. **幻觉率的行业水位**：通用 LLM 在法律任务上 58–88%；商用法律工具（Lexis+ AI / Westlaw AI / Practical Law）**仍有 17–33% 幻觉引用**；优化后的 RAG（BM25+GPT-5）可到 8%；Legal RAG Bench 上 Gemini 3.1 Pro 平均 5.7%、GPT-5.2 为 11.3%。
4. **领域嵌入模型收益显著**：Kanon 2 Embedder 相对通用嵌入，准确率 +17.5、groundedness +4.5、检索准确率 +34。
5. **失败模式是 misgrounding（错误锚定）**：引用真实法条但不支持所生成结论 —— 比"编造法条"更隐蔽。
6. **定价正在从订阅转向按用量**：Legora 已转 consumption-based；Harvey / Thomson Reuters 以自建模型维持固定订阅。中国市场"自下而上"渗透：智合AI B 端订单几乎都由内部个人用户先推动。
7. **私有化是前提**：法义经纬（WAIC 2026）法律 AI 一体机、Enclave、LQ.AI 均以"数据不出内网"为第一卖点。

### 4.3 最被低估的风险：**数据许可**

`corpus_registry` 表自己写着：

```
court_cases_opensource  121万条
  commercial_use: 开源数据集，多为研究许可；商用前需逐源复核许可
  note: ⚠ 案例类数据商用许可需逐一核验，不可默认视为可商用
```

这是**目前最大的商业化阻塞点**，而不是技术：

- 121 万"案例"里，`court_cases` 的真实构成是 HuggingFace 数据集标签级记录
  （`case_number` 形如 `HF-97de3926bbfd5054`、`court_name` 为空），**不是可展示的裁判文书**
- 裁判文书网自 2021 年起限制批量获取，**爬取方式商用存在法律风险**
- 15 GB 合成数据的案号/法院均为编造 —— 一旦被展示，构成**虚假引用**，对律所是执业责任问题

**结论：数据量不是"如何扩充到上亿"的问题，而是"哪一亿条能合法商用"的问题。**

---

## 五、"上亿数据"的诚实回答

### 5.1 现状

| 口径 | 数量 |
| --- | ---: |
| 库内真实数据 | ≈ 298 万 |
| 其中**可引用**（法条+法规+司法解释） | ≈ 70 万 |
| 磁盘合成数据（不可引用、未入库） | 1500 万 |
| **宣称目标** | 1 亿 |

**距目标差约 33 倍（按库内真实数据）/ 无法用合成数据合法填充。**

### 5.2 可行的扩充路径（按性价比排序）

**第一优先 — 把已有真实数据做"薄"而非做"多"**
- ✅ **已完成**：`judicial_interpretations` 由 **0 行 → 219 行**
  （225 条源记录按名称去重，重复名保留正文最全的一份），
  72.7 万字正文、74 条带 文号、141 条带关联法条。
  脚本：`backend/scripts/populate_judicial_interpretations.py`（幂等，支持 `--dry-run`）
- 补 `legal_concepts`（18 条）：法条交叉引用 20,219 条已可支撑概念抽取
- 修复经典案例抽取：121 万条中筛出有完整裁判理由的部分，比新增 1000 万条噪声更有价值

**第二优先 — 合法公开数据源**
- 国家法律法规数据库（flk.npc.gov.cn，约 2.65 万件）—— 已有爬虫
- 最高法指导性案例、公报案例（数千件，权威性最高）
- CAIL / LAIC 等学术数据集（研究许可，用于**评测与微调**，不作引用）
- 各省高院官网公布的典型案例

**第三优先 — 商业授权（要花钱，但这是唯一合规的"上亿"路径）**
- 北大法宝 / 威科先行 / 法信等商业法律数据库授权
- 中国司法大数据研究院（1.5 亿文书 + 6000 万裁判规则 + 450 万法规数据集）
- 这是**采购与法务谈判问题，不是技术问题**

**不要做** — 用模板合成数据充数。理由见 §4.3，风险远大于收益。

### 5.3 结论

**把"上亿"作为产品指标是错的。** 律所和企业法务的采购标准是
**"引用是否可溯源、是否准确、是否有商用许可"**，不是"库里有几亿条"。
行业水位是 17–33% 幻觉引用率仍然卖得动，说明**可验证性比数据量更有说服力**。

建议把对外指标改为：
- ✅ "引用全部可溯源至权威公开来源，逐条给出置信度"
- ✅ "在 N 题法律基准上幻觉引用率 X%（附评测脚本可复现）"
- ❌ 不要再提"上亿数据"

---

## 六、侧边栏"深度推理 / 技能包 / 工具调用"是否多余

**结论：三者并不等价 —— 一个应删，一个应改，一个应留。**

| 入口 | 与对话页的关系 | 判定 |
| --- | --- | --- |
| **工具调用**（`/tools`，ToolsView） | 与对话页"MCP工具"popover **用同一个** `mcpApi.listTools`；但把工具执行结果以**原始 JSON** 呈现 | ⚠️ **最应移出用户侧边栏** |
| **技能包**（`/skills`，SkillsView） | 列表与对话页"技能包"popover 同源；SkillsView 独立同步执行并展示 `final_summary` | 🔶 **保留但降级为"浏览/发现"**，执行应回到对话 |
| **深度推理**（`/deep-think`，DeepThink） | 对话页的"深度思考"只是把推理结果注入回答；DeepThink 才是**唯一可视化思维链**（6 步流程、IRAC 四段、置信度仪表盘、8 类 SSE 事件） | ✅ **不重复，应保留** |

### 6.1 为什么"工具调用"应移出

1. **它是开发者调试界面，不是律师界面**：直接输出 `/mcp/tools/execute` 的原始 JSON。
   律师不需要看 JSON。
2. **它会展示编造数据**：`enterprise_lookup` 在未配置 key 时返回
   `{"note":"企业工商查询API尚未配置","source":"demo_mode"}` 的假企业信息（§3.2）。
3. **完全被对话页覆盖**：对话页 popover 已能选工具、且由 LLM 编排调用，
   结果会经过引用核验、水印、安全过滤 —— 比裸调用更合规。

**建议**：移到 `/admin/tools`（管理员调试页），或直接删除。

**✅ 已执行**：路由改为 `/admin/tools`，并加 `requiresAdmin` 守卫；
旧路径 `/tools` 保留重定向，避免已收藏的链接失效。
侧边栏从主菜单（律师可见）移到底部管理区，仅 `role === 'admin'` 显示。
`stores/auth.ts` 的 `UserInfo` 补上 `role` 字段与 `isAdmin` 计算属性
（后端 `/auth/me` 本来就返回 role，只是前端类型没声明，因此之前无法做角色判断）。
守卫是懒加载 store 的：直接刷新 `/admin/*` 时 store 里还没有用户资料，
必须先 `fetchUser()` 再判断，否则管理员会被误挡。

**✅ 复核后补修（同日）**：上述迁移做完了，但留下 2 个缺口，已修复。

1. **管理员校验只做了前端，后端没做**（`app/api/v1/mcp_tools.py`）。
   §6.1 的整段理由是"律师不该看到 `demo_mode` 编造的企业信息"，而当时只关了
   界面：任何已登录的**普通律师**仍可直接
   `POST /api/v1/mcp/tools/execute` 拿到那份假数据。匿名洞此前已堵（返回 401），
   **越权洞一直开着**。

   修法有坑：**不能整个 router 加 admin** —— 对话页的 MCP popover 用的是同一个
   `GET /mcp/tools`（`ChatView.vue`），收归 admin 会把聊天页弄挂。因此只把
   `POST /tools/execute` 与 `/tools/execute-batch` 换成 `get_current_admin_user`
   （该依赖已存在于 `api/deps.py`），发现类端点保持对已登录用户开放。

   这个划分是安全的，因为对话页的 LLM 工具调用**不走这两个 REST 端点** ——
   它走进程内 `services/tool_orchestrator.py` → `get_tool_registry()`。

2. **侧边栏高亮返错值**（`components/AppLayout.vue`）。两处：
   `activeMenu` 里残留 `if (path.startsWith('/tools')) return '/tools'` ——
   `'/admin/tools'.startsWith('/tools')` 为 **false**，会一路落到 `return '/chat'`，
   于是管理员打开工具页时高亮的是"智能对话"，"工具调用（管理）"**永不高亮**；
   且底部管理区那个 `<el-menu>` 当时**根本没绑 `:default-active`**，
   即使返对了值也不会高亮。两处均已修。

**新增回归测试**：`backend/tests/test_admin_tool_gate.py`（6 个），锁住这个安全边界。
用"不存在的工具名"区分「被鉴权拦下」与「通过了鉴权」：管理员走通鉴权后拿到 404，
非管理员被挡在 403，全程**不触发真实工具执行**（不产生 LLM 调用与费用）。

### 6.2 为什么"技能包"应改而非删

技能的价值在**发现**（19 个技能包，用户不知道有哪些），这个 SkillsView 做得好。
但**执行**走独立同步接口就绕开了对话页的引用核验与水印链路。
建议：SkillsView 只做目录浏览，"使用此技能"按钮跳回对话页并预选该技能。

### 6.3 为什么"深度推理"应留

对话页的深度思考只影响 Prompt 注入；DeepThink 提供**过程可见**——
这在法律场景是核心卖点（"可解释性"是律所采购的关键指标）。
本次已为对话页补上推理步骤面板，两者是"快速问答"与"专业论证"的分工，不是重复。

---

## 七、接下来该做什么（按优先级）

### P0 — 不解决就无法商业化（1–2 周）

1. **把项目纳入版本管理**
   当前仓库根是 `D:/`，追踪的是另一个项目，本项目 **0 文件被追踪**。
   ```bash
   cd "D:/Legal Intelligent Assistance System"
   git init && git add -A && git commit -m "baseline"
   ```
   在此之前，**任何删除操作都不可回滚**。
2. **数据许可尽调**：逐源核验 `court_cases` 121 万条的商用许可；无法核验的立即从
   可引用集合中摘除（`corpus_registry.citable` 已提供机制）。
3. **隔离 15 GB 合成数据**：移出 `backend/data/`，防止被误索引进法律语料。
4. **修掉会返回假数据的工具**：`enterprise_lookup` 未配置时**必须报错**，不能返回 demo 数据。
5. **补建镜像**：`docker compose build backend`，否则本次修复在下次重建时丢失。
   **⚠️ 仍未执行（同日复核）**：后端代码**烘焙进镜像**（compose 里 backend 只有
   `backend_uploads` / `hf_cache` / `whisper_models` 三个卷，**无代码 bind mount**），
   两个副本跑的仍是旧代码；前端 `dist/` 同样是 `COPY` 进镜像的。
   前端已 `npm run build` 并验证产物（`vue-tsc` 类型检查通过，新 chunk
   `AppLayout-Cw7JOPwL.js`），**但容器也未更换**。需执行：
   ```bash
   docker compose build backend && docker compose up -d --force-recreate --no-deps backend
   docker compose build frontend && docker compose up -d --force-recreate --no-deps frontend
   ```
   在此之前，**§6.1 的后端 admin 校验在前端界面之外尚未生效**。

### P1 — 商业化必要条件（1–2 月）

6. **真实 MCP 支持**：接 `mcp` Python SDK（stdio + streamable HTTP），
   让外部 MCP Server 可挂载 —— 这是与主流产品的架构对齐点。
7. **评测闭环**：把 `run_hallucination_eval.py` 接进 CI，产出可对外的
   **幻觉引用率 / 引用准确率**基线数字（当前 50 题数据集已有，只是没跑通）。
8. **检索质量**：CPU 下 reranker 被跳过 → 需要 GPU 节点或换轻量 reranker；
   恢复 Neo4j 图谱检索与 ES 全文检索（两条已写好的路径当前不可用）。
9. **激活 19 个技能**：`total_executions: 0` 说明从未被真实使用，需端到端验收。
10. **删除/改造重复入口**（§6）。
    - ✅ **工具调用**：已移入 `/admin/tools`，前端守卫 + **后端 admin 校验**均已补齐（§6.1）。
    - ❌ **技能包**：**尚未改造**。`SkillsView.vue` 仍是"开始执行 → 同步执行接口 →
      直接渲染 `final_summary`"，即 §6.2 要去掉的那条**绕开引用核验与水印**的链路。
      目标形态：只做目录浏览，"使用此技能"跳回对话页并预选；这需要 `ChatView.vue`
      新增 `route.query` 技能预选（该文件当前**没有任何** `useRoute` 处理，需新建）。
    - ✅ **深度推理**：确认保留，不重复（§6.3）。
11. **多租户唯一约束**修正 + RLS 启用。

### P2 — 规模化与商业闭环（3–6 月）

12. 私有化交付形态：k8s / 一体机 / 气隙部署方案 + 数据不出内网证明材料
13. 计量计费（按席位/按用量）
14. 领域微调（LoRA）真正跑通
15. 企业知识库落地（当前 `user_documents` 仅 4 条）
16. 前端代码分割（主包 1.25 MB）
17. 合规对齐文档（SOC 2 / ISO 27001 / 等保）

---

## 八、距离商业化还有多远

| 维度 | 完成度 | 说明 |
| --- | --- | --- |
| 功能覆盖 | **75%** | 用户列举的合同全生命周期/诉讼支持/合规/多智能体**都已存在** |
| 工程健壮性 | **60%** | 测试 146 个通过，但无 CI、无版本管理 |
| 数据合规 | **20%** | ⚠️ **最大短板**，121 万案例许可未核验 |
| 评测可证明性 | **15%** | 有工具无数字 |
| 生态集成 | **25%** | 无真实 MCP |
| 私有化交付 | **40%** | 单机 Docker |
| 商业闭环 | **5%** | 无计费 |

**综合：约 30% 商业化就绪。**

**最诚实的判断**：技术上这个项目比大多数 demo 走得远得多 ——
多智能体、字段级加密、多租户、引用闸门、AIGC 水印都是真实实现，
不是贴标签。**瓶颈已经从"能不能做出来"转移到了"能不能合规地卖出去"**。

按 P0 五项做完（约 1–2 周），就可以开始**面向 1–2 家种子律所做付费 PoC**；
P1 做完（1–2 月）具备区域市场交付能力。**数据许可这一项如果过不了，
后面所有工作都无法变现** —— 建议优先处理。

---

## 附：本次变更文件

### 首轮（功能与安全缺陷修复）

```
backend/app/mcp/__init__.py              政府法规工具改查 laws；wenshu 民事超集
backend/app/mcp/server.py                3 端点加鉴权
backend/app/api/v1/router.py             11 路由统一加鉴权（默认保护）
backend/app/api/v1/data_expansion.py     /data/stats 改为实测语料表
backend/app/api/v1/deep_think.py         depth 参数贯通
backend/app/agents/deep_thinking_agent.py 新增 _depth_policy，真实控制推理深度
backend/app/rag/document_processor.py    真实 OLE2 .doc 解析 + 垃圾文本闸门
backend/tests/test_document_parsing.py   新增 23 个解析回归测试
frontend/src/components/ChatMessage.vue  新增 IRAC 推理过程面板
frontend/src/api/index.ts                透传 model_override / depth
frontend/src/views/DeepThink.vue         发送 model_override / depth
frontend/nginx.conf                      修复安全头丢失 + 允许麦克风
```

> **清单更正**：下列 3 个文件当时**也改过但漏记**（§6.1 的迁移实际由它们完成）：
> ```
> frontend/src/router/index.ts             /admin/tools 路由 + requiresAdmin；/tools 保留重定向
> frontend/src/components/AppLayout.vue    工具调用移出主菜单 → 底部管理区，仅 isAdmin 可见
> frontend/src/stores/auth.ts              UserInfo 补 role 字段 + isAdmin 计算属性
> ```

**首轮验证**：146 测试通过（含 23 新增，但见 §1.1 的可复现性存疑）；双副本 8/8 无 token
请求返回 401；`/data/stats` 返回真实 2,984,229；政府法规工具返回真实司法解释；
案例检索由 0 → 32 条；6 项安全头全部下发。

### 同日复核补修（§6.1 的两个遗留缺口）

```
backend/app/api/v1/mcp_tools.py           execute / execute-batch 加 get_current_admin_user
                                          （发现类端点保持开放，对话页 popover 依赖）
backend/tests/test_admin_tool_gate.py     新增 6 个鉴权回归测试（admin 404 / user 403 / 匿名 401）
frontend/src/components/AppLayout.vue     activeMenu 用完整路径匹配 '/admin/tools'；
                                          底部管理区 menu 补 :default-active="activeMenu"
```

**补修验证**：`test_admin_tool_gate.py` 6/6 通过（连跑 3 次稳定）；
`tests/test_commercial_modules.py` 37 passed；前端 `npm run build`（含 `vue-tsc`）
通过，产物内已确认 `admin/tools` 匹配存在、旧错误分支消失。
**未执行**：镜像重建（§7-P0-5），故后端校验尚未在运行环境中生效。
