# 多智能体编排框架（P1.6）技术文档

> 状态: ✅ 已完成并通过 E2E 验证（10/10）
> 最后更新: 2026-09-12

## 1. 概述

P1.6 将系统中已有的 7 个专业法律代理（检索、咨询、合同审查、合同起草、合规风险、诉讼支持、文书生成）统一接入一个可编排的协作框架，支持 4 种协作模式，并集成到独立 API 与主聊天管线两条路径。

**解决的问题**：单个代理只能处理单一领域任务。当用户提出"审查这份数据处理协议并评估合规风险"这类跨领域复合问题时，需要多个代理分工协作并综合结论。

**核心文件**：

| 文件 | 职责 |
|------|------|
| `backend/app/agents/collaboration.py` | 核心编排器：Agent 注册表、4 种协作模式、LangGraph 工作流 |
| `backend/app/api/v1/collaboration.py` | REST 端点（analyze / research / review / patterns） |
| `backend/app/schemas/collaboration.py` | 请求/响应 Pydantic 模型 |
| `backend/app/agents/supervisor_agent.py` | Supervisor 路由：复杂查询 → 协作节点 |
| `backend/app/api/v1/chat.py` | 聊天管线集成：`enable_multi_agent` 开关 |
| `backend/app/schemas/chat.py` | `ChatRequest.enable_multi_agent` 字段 |
| `backend/tests/test_collaboration.py` | 单元 + 集成测试 |
| `backend/scripts/e2e_p16_multiagent.py` | E2E 验证脚本（10 项检查） |

## 2. 架构总览

```
                         ┌─────────────────────────────┐
   POST /api/v1/chat     │        nginx (300s)         │
   enable_multi_agent    └──────────────┬──────────────┘
                                        │
                 ┌──────────────────────▼──────────────────────┐
                 │  chat.py 流式管线                            │
                 │  1. 协作报告生成（MultiAgentCollaborator）    │
                 │  2. 报告剥离免责声明后拼入 RAG 上下文          │
                 │  3. 主管线统一处理引用核验/水印/安全检查       │
                 └──────────────────────┬──────────────────────┘
                                        │
   POST /api/v1/collaboration/analyze   │
                 ┌──────────────────────▼──────────────────────┐
                 │  SupervisorAgent（LangGraph）                │
                 │  intent=complex_analysis → 协作节点           │
                 └──────────────────────┬──────────────────────┘
                                        │
                 ┌──────────────────────▼──────────────────────┐
                 │  MultiAgentCollaborator（LangGraph）         │
                 │                                             │
                 │  classify ──► route(pattern)                │
                 │    ├─ parallel      → parallel_execute ─┐   │
                 │    ├─ sequential    → sequential_exec ─┤   │
                 │    ├─ iterative     → draft→review→revise   │
                 │    └─ hierarchical  → decompose → parallel ─┤
                 │                                          ▼   │
                 │                                     synthesize │
                 └──────────────────────┬──────────────────────┘
                                        │  统一适配器契约
                 ┌──────────────────────▼──────────────────────┐
                 │  AGENT_REGISTRY（7 个专业代理）               │
                 │  law_retrieval / legal_consult /            │
                 │  contract_review / contract_draft /         │
                 │  compliance_risk / litigation_support /     │
                 │  document_gen                               │
                 └─────────────────────────────────────────────┘
```

## 3. Agent 注册表

### 3.1 统一适配器契约

所有代理通过同一签名接入，编排器对代理实现完全无感知：

```python
AgentAdapter = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
# (query, context) -> result dict
```

结果文本由 `_text_of()` 按以下顺序提取（兼容各代理不同的返回字段）：
`final_output` → `report` → `analysis` → `content` → `final_response` → JSON 序列化截断。

### 3.2 已注册代理

| Agent ID | 描述 | 底层实现 | 接受的 context 参数 |
|----------|------|----------|---------------------|
| `law_retrieval` | 法律检索：法条、司法解释与案例 | `create_law_retrieval_agent()` | `category_filter` |
| `legal_consult` | 法律咨询：基于检索结论解答问题 | `create_legal_consult_agent()` | `prior_findings`（串行模式自动注入） |
| `contract_review` | 合同审查：风险识别、等级评定、修改建议 | `create_contract_review_agent()` | `review_focus` |
| `contract_draft` | 合同起草：模板优先，LLM 兜底 | `ContractLifecycleAgent.draft_contract()` | `contract_type` |
| `compliance_risk` | 合规风险评估、法规映射、整改建议 | `ComplianceRiskAgent.risk_assessment()` | `industry`、`compliance_domains` |
| `litigation_support` | 案由分析、诉讼策略、证据要求 | `LitigationSupportAgent.analyze_case()` | `evidence_list`、`claims` |
| `document_gen` | 文书生成：起诉状、答辩状、律师函等 | `create_document_gen_agent()` | `document_type` |

### 3.3 注册新代理

```python
from app.agents.collaboration import register_agent

async def my_adapter(query: str, context: dict) -> dict:
    result = await MyAgent().run({"query": query})
    result.setdefault("final_output", result.get("report", ""))
    return result

register_agent("my_agent", "一句话能力描述（供 LLM 分类器选择）", my_adapter)
```

设计要点：
- **懒加载**：代理实例在适配器内部首次调用时创建，模块导入时不触发 LLM/模型预热
- **注册即路由**：新代理注册后自动出现在 LLM 分类器的可用代理清单中（`available_agents_description()`），无需改编排器代码
- 适配器需将主文本放入 `final_output`（或上述提取顺序中的任一字段）

## 4. 四种协作模式

| 模式 | 机制 | 适用场景 |
|------|------|----------|
| `parallel` 并行扇出 | 全部代理经 `asyncio.gather` 并发执行，结果合并综合 | 多领域并行调研 |
| `sequential` 串行流水线 | 按序执行，前序结论（截断 2000 字符）注入 `prior_findings` 传给下一代理 | 先检索后分析等依赖链 |
| `iterative` 迭代精修 | 初稿 → 质量评审 → 修订循环，直至通过 | 高风险输出需质量保障 |
| `hierarchical` 层级分解 | LLM 将任务拆解为代理标签子任务 → 并发执行 → 汇总 | 需跨域拆解的复杂事项 |

### 4.1 LangGraph 拓扑

```
classify ──┬─ parallel      → parallel_execute ──────────────→ synthesize → END
           ├─ sequential    → sequential_execute ────────────→ synthesize → END
           ├─ iterative     → iterative_draft → iterative_review ─┬─(approved)→ synthesize → END
           │                                       ▲             └─(revise)→ iterative_revise ┘
           │                                       └────────────────────┘
           └─ hierarchical  → hierarchical_decompose → parallel_execute → synthesize → END
```

### 4.2 迭代模式的评审门控

`iterative_review` 对初稿评分（quality_score 0-100、issues、suggestions），门控规则（满足其一即通过）：

1. 评审显式 `approved: true`
2. `quality_score >= 80`
3. 达到 `max_iterations`（默认 3 轮）

修订提示词强制保持法条引用格式 `（《X法》第X条）`。

### 4.3 单代理降级规则

LLM 分类若只选出 1 个代理且模式为 `parallel`/`hierarchical`，自动降级为 `sequential`——单代理无编排收益。**显式 context 指定不降级**（尊重调用方的确定性意图）。

## 5. 任务分类：LLM 与显式覆盖

### 5.1 LLM 自动分类（默认路径）

`classify` 节点用 temperature=0 的 LLM 输出 JSON：

```json
{
    "intent": "contract_analysis_with_compliance",
    "required_agents": ["contract_review", "compliance_risk"],
    "collaboration_pattern": "parallel",
    "primary_agent": "contract_review"
}
```

- 代理数上限 4；无效代理 ID 被过滤；分类失败兜底为 `["law_retrieval", "legal_consult"]` + `parallel`
- `hierarchical` 模式下另由 LLM 拆解子任务（2-4 个），拆解失败则退化为按代理原样分派

### 5.2 显式 context 覆盖（确定性路径）

调用方在 `context` 中同时提供 `required_agents`（含有效 ID）+ `collaboration_pattern`（合法模式）时，**完全跳过 LLM 分类**，按指定值执行。适用于测试与生产侧的确定性编排控制。

`hierarchical` 模式还支持显式 `context["subtasks"] = [{"agent": "...", "task": "..."}]` 跳过 LLM 拆解。

## 6. REST API（前缀 `/api/v1/collaboration`）

### 6.1 `GET /patterns` — 列出协作模式

```json
{"patterns": [{"id": "parallel", "name": "Parallel Fan-out", "description": "...", "use_case": "..."}, ...]}
```

### 6.2 `POST /analyze` — 多智能体复杂分析

```json
{
    "query": "审查这份数据处理协议并评估合规风险",
    "context": {
        "required_agents": ["contract_review", "compliance_risk"],
        "collaboration_pattern": "parallel"
    }
}
```

响应字段：`final_response`（综合报告，含法律声明）、`intent`、`collaboration_pattern`、`agents_involved`、`iterations`、`disclaimer`。省略 context 时走 LLM 自动分类。

### 6.3 `POST /research` — 深度法律研究

由 `DeepResearchAgent` 执行：法条 + 案例 + 司法解释 + 知识图谱综合检索，返回 `relevant_laws / relevant_cases / judicial_interpretations / analysis / confidence / sources_cited`。

### 6.4 `POST /review` — 质量评审

`review_type` 支持 `general / legal_response / contract_analysis / document`，返回 `quality_score / issues / suggestions / corrected_text / citation_check / completeness_check`。

## 7. 聊天管线集成（`enable_multi_agent`）

`POST /api/v1/chat/chat/stream` 请求体新增 `enable_multi_agent: true` 后的处理链：

1. **缓存旁路**：与深度思考/联网搜索相同，多智能体请求不命中语义缓存（`use_cache = False`）
2. **协作执行**：`MultiAgentCollaborator.run()` 生成综合报告
3. **免责声明去重**：协作报告自带的 `【法律声明】` 段被剥离，避免与主管线输出重复
4. **拼接 RAG 上下文**：报告以 `## 多智能体协作分析报告` 标题拼入 RAG 上下文，后续引用核验、水印、安全检查全部走主管线
5. **流式 meta 事件**：`meta` 事件包含 `features.multi_agent: true` 与执行详情：

```json
{"multi_agent": {"pattern": "parallel", "agents": ["law_retrieval", "legal_consult"], "agent_details": [...], "iterations": 0}}
```

协作失败时记录 warning 并降级为标准管线（不阻断对话）。

## 8. Supervisor 集成

`SupervisorAgent` 的 LangGraph 中，意图分类为 `complex_analysis`（"涉及多个法律领域的复杂问题"）时路由到 `execute_complex_analysis` 节点：

```python
result = await self.collaborator.run(query=state.user_query, context=state.context)
result["final_output"] = result.get("final_response", "")  # 聚合节点读取 final_output
```

注意字段映射：协作器返回 `final_response`，Supervisor 聚合节点读取 `final_output`（曾有回归 bug，已有测试覆盖）。

## 9. 容错与可观测性

- **单代理失败隔离**：`_execute_agent` 捕获各代理异常，记录到 `agent_details`（`status: error` + 错误摘要），其余代理继续执行
- **执行详情**：每个代理记录 `agent` / `status` / `elapsed_ms` / `preview`（200 字符预览），随 API 响应与流式 meta 透出
- **LLM 解析兜底**：分类/拆解/评审的 JSON 解析失败均有安全默认值，不中断流程
- **日志**：编排各环节经 `app.agents.collaboration` logger 输出，集成点经 chat logger 输出

## 10. 引用与幻觉约束（继承系统硬约束）

综合提示词显式要求：

1. 法条引用格式必须为 `（《X法》第X条）`
2. 无检索依据时必须声明"未检索到直接相关的法律条文"，禁止编造法条
3. 所有报告末尾附加 `LEGAL_DISCLAIMER`（法律声明）

聊天管线集成时，协作报告经由主管线的引用核验（`citation_verifier`）二次校验。

## 11. 测试

### 11.1 单元/集成测试（`backend/tests/test_collaboration.py`）

覆盖：注册表契约、4 种模式执行路径、显式 context 覆盖、单代理失败隔离、Supervisor 字段映射回归、免责声明存在性。

**推荐运行方式：独立临时容器**（2026-09-12 实测 99 通过 / 1 跳过 / 0 失败）。

> ⚠️ 不要在生产 backend 容器内跑全量 pytest：容器 4GB 内存上限由 uvicorn + BGE-M3/Reranker 常驻模型共享，pytest 加载完整 app（含模型单例）会触发 OOM 被杀。

```powershell
docker run --rm --name p16-regression --memory 4g `
  --network legal_network `
  --env-file "d:\Legal Intelligent Assistance System\backend\.env" `
  -v "d:\Legal Intelligent Assistance System\backend\tests:/app/tests" `
  -v "d:\Legal Intelligent Assistance System\backend\pytest.ini:/app/pytest.ini" `
  --entrypoint sh legalintelligentassistancesystem-backend:latest `
  -c "cd /app && python -m pytest tests/ -q"
```

说明：
- `--memory 4g` 独占内存配额，与线上双后端互不影响
- `--network legal_network` + `--env-file` 使测试进程能以与生产一致的配置访问 Redis/PG/Milvus
- pytest / pytest-asyncio / aiosqlite 已随 `requirements.txt` 固化进镜像（>=7.4 / >=0.23 / >=0.20），无需临时 pip install
- 快速子集验证：将 `tests/` 换成 `tests/test_collaboration.py`（实测 14 passed, 0.52s）

### 11.2 E2E 验证（`backend/scripts/e2e_p16_multiagent.py`，通过 nginx 对生产链路实测）

| # | 检查项 |
|---|--------|
| 1 | 注册/登录测试账号 |
| 2 | GET /collaboration/patterns 返回 4 种模式 |
| 3 | POST /analyze 自动分类并执行（≥2 代理、实质内容） |
| 4 | 协作报告包含法律声明 |
| 5 | 协作在 300s 超时内完成 |
| 6 | 显式 context 确定性执行（sequential 检索→咨询） |
| 7 | POST /chat/stream (enable_multi_agent) 流式返回 |
| 8 | meta 标记 multi_agent 特性已启用 |
| 9 | meta 包含执行详情（pattern + agents） |
| 10 | 流式回复包含实质法律内容 |

实测结果：**10/10 通过**（2026-09-12，通过 `http://localhost/api/v1`；最终镜像滚动上线后复验再次 10/10，含 54s 自动分类协作 / 27s 显式覆盖 / 96s 多智能体流式对话）。

## 12. 部署说明

- 镜像：P1.6 代码 + 测试依赖（pytest/pytest-asyncio/aiosqlite，随 `requirements.txt` 固化）已构建进 `legalintelligentassistancesystem-backend:latest`（2026-09-12 20:12）
- 双后端容器（backend-1 / backend-2）已运行与最新镜像逐文件校验一致的代码（全量 `app/**/*.py` md5 比对通过），nginx `/api/` 超时 300s 已覆盖协作耗时
- 开发期热更新仍可用 `docker cp` + 容器重启，但正式发布前必须重建镜像

## 13. 已知限制与后续计划

1. **子任务粒度**：hierarchical 拆解依赖 LLM 质量，子任务间无依赖建模（全部并发）
2. **迭代模式成本**：每轮评审+修订增加 2 次 LLM 调用，max_iterations=3 时最多 7 次调用
3. **前端**：`enable_multi_agent` 开关尚未在前端 UI 暴露（API 已就绪）
4. **后续方向**：与 P1.4 诉讼深化打通（litigation_support 代理输出结构化）、协作过程流式透出（当前仅透出最终报告）
