# Phase 3 完成报告：MCP + 技能包深度集成

**完成时间**: 2026-09-03
**状态**: ✅ 已完成并验证

---

## 一、核心变化：从关键词匹配到 LLM 自主决策

Phase 3 之前，聊天中的工具调用是**关键词子串匹配 + 字符串切片提参**：

```python
# 旧实现（app/api/v1/chat.py）
_TOOL_KEYWORD_MAP = {"企业": {...}, "计算赔偿": {...}, ...}
for keyword, cfg in _TOOL_KEYWORD_MAP.items():
    if keyword in query:                       # 子串命中即触发
        after = query[idx + len(keyword):]     # 切片提参
        value = after.split("，")[0].split("的")[0]
```

问题是：关键词表必须穷举所有说法。"请计算我应得的赔偿金"不含"计算赔偿"连续子串，
"诉讼费要交多少"不含"诉讼费用"，这些查询都不会触发任何工具。

现在改为 **LLM 看到工具目录后自行决定调哪些工具、传什么参数**，选中的工具并发执行。

---

## 二、交付内容

### 1. 新增 `backend/app/services/tool_orchestrator.py`

| 函数 | 职责 |
|------|------|
| `build_tool_catalogue()` | 渲染工具目录（含参数类型/必填/枚举）供选择提示使用 |
| `select_tools()` | LLM 决策：返回 `[{tool, arguments, reason}]`，可返回空列表 |
| `execute_tool_calls()` | `asyncio.gather` 并发执行，逐工具超时隔离 |
| `format_tool_results()` | 结果渲染为提示片段，失败/空结果明确要求模型据实说明 |
| `orchestrate()` | 选择+执行一体入口，任何环节失败都降级为"不用工具" |
| `run_skill_for_chat()` | 技能包接入聊天，按技能自身 input_schema 填参 |

关键约束：
- `MAX_TOOLS_PER_QUERY = 3` — 每个工具是一次 DB/网络往返，无上限扇出会拖垮延迟
- `TOOL_TIMEOUT_SECONDS = 20` — 单个慢工具不阻塞整个回答
- 选择结果经注册表校验，丢弃 LLM 幻觉出的工具名和越权工具

### 2. 修改 `backend/app/api/v1/chat.py`

- 新增 `_may_need_tools()` 门控，替代原 `classify_intent` 单一判据
- MCP 工具块替换为 `orchestrate()` 调用
- **新增技能包执行**：`payload.skill_id` 此前被 schema 接收但从未使用（`chat.py` 中零引用），前端技能选择器对后端无任何影响
- SSE meta 事件新增 `tool_orchestration` / `skill_execution`，前端可显示实际执行情况
- 元数据一并写入消息记录

---

## 三、修复的三个真实缺陷

### 缺陷1 — 旧工具调用代码序列化错误对象

```python
result = await tool.execute(query=sanitized_message[:500])
tool_parts.append(f"...{json.dumps(result, default=str)[:3000]}")
```

两处错误：`execute()` 返回 `ToolResult` 数据类，`json.dumps(..., default=str)`
序列化的是对象字符串而非 `result.data`；且不论工具实际参数 schema 如何，
一律硬传 `query=` 关键字。

### 缺陷2 — 门控判据被口语归一化破坏

工具编排在 `classify_intent` 之前运行，我最初复用它做门控。但此时
`sanitized_message` 已被口语归一化 Agent 改写为正式法律表述，
"请计算我应得的赔偿金"变成无"计算"触发词的formal句式 —— 明显需要计算器的查询
被判为普通咨询，工具从未被尝试（测试中 `tool_orchestration` 字段完全缺失）。

**修复**：门控改判 `payload.message` 原文；并将判据从单一关键词扩展为
"事实具体性信号"（数字、查询动词、数量疑问、实体标记、条号正则），
真正的取舍交给选择 LLM。

### 缺陷3 — `law_article_search` 条号精确匹配失效（关键）

```python
conditions.append("la.article_number = :article_number")   # 严格相等
```

数据库存储中文数字 `第四十七条`，而工具参数文档写的是 *"如'第143条'"*（阿拉伯数字）
—— 文档推荐的格式恰好是查不到的那个。实测：

```
第四十七条 -> 2 rows
第47条     -> 0 rows     <-- LLM 按文档传参即命中此路径
```

更糟的是 `keyword` 与 `article_number` 是 AND 关系，关键词若不在该条正文中同样清零结果。

**修复**：
1. 新增 `_int_to_cn()` / `_normalise_article_number()`，中文与阿拉伯数字互转后 OR 匹配
2. 千位采用真正的位值记法（`1079 → 第一千零七十九条`，非逐字 `第一零七九条`）
3. 指定条号时不再 AND 关键词条件
4. 结果按 (法律名, 条号) 去重 —— 语料存在重复导入行
5. 参数说明改为"中文或阿拉伯数字均可"

**转换正确性校验**：导出民法典 1257 条实际条号标签，对 1–1260 全量比对，
仅 3 条不匹配（340/342/966），经查这 3 条**在语料中本就缺失**（1257 存储 vs 1260 应有），
属数据完整性问题而非转换错误。即转换在所有存在的条文上 **1257/1257 正确**。

---

## 四、验证结果

### 工具选择判别力：9/9

| 期望调用 | 查询 | 实际选中 |
|---------|------|---------|
| ✅ | 标的额50万的民事诉讼，诉讼费要交多少？ | `legal_calculator` |
| ✅ | 查一下阿里巴巴这家公司的工商注册信息 | `enterprise_lookup` |
| ✅ | 我月薪12000元，工作5年被违法辞退，赔偿金是多少？ | `legal_calculator` |
| ✅ | 借款10万元，年利率15%，逾期2年，利息该算多少？ | `legal_calculator` |
| ✅ | 劳动合同法第四十七条的原文是什么？ | `law_article_search` |
| ❌ 不应调用 | 什么是无固定期限劳动合同？请解释其法律含义。 | （无） |
| ❌ 不应调用 | 请解释善意取得制度的构成要件 | （无） |
| ❌ 不应调用 | 民事责任和刑事责任的区别是什么？ | （无） |
| ❌ 不应调用 | 签合同时应该注意哪些法律风险？ | （无） |

判别是真实的：概念题一律不调工具，事实题准确选中对应工具。选择耗时 0.6–0.9s。

### 法条原文查询修复前后对比

修复前，模型只能对冲并凭记忆复述：
> **未能从法律知识库中检索到...第四十七条的条文原文。** 外部工具检索结果（`law_article_search`）返回为空（`"articles": []`）... 可作以下**一般性法律常识**说明（非条文原文）

修复后返回逐字原文，无任何对冲措辞：
> 根据《中华人民共和国劳动合同法》第四十七条规定：
> > **经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资的标准向劳动者支付。六个月以上不满一年的，按一年计算；不满六个月的，向劳动者支付半个月工资的经济补偿。**

`民法典第1079条` 同样正确返回诉讼离婚条文。

### 技能包接入

- 7 个技能包全部可用：`labor_dispute` / `contract_analysis` / `ip_protection` / `criminal_defense` / `enterprise_compliance` / `litigation_preparation` / `debt_recovery`
- `skill_id='labor_dispute'` 执行 6 步成功（62.6s），结果并入回答
- 未知 skill_id 优雅降级：`success=False`，聊天仍正常作答（2278 字符）

### 回归

Phase 2 上下文管理未受影响：5 轮消息数 2/4/6/8/10 全部正确。

---

## 五、已知限制

1. **技能包执行耗时长**（labor_dispute 6 步约 63 秒）。技能内部是串行 LLM 调用，
   对交互式聊天偏慢，应改为流式或后台任务。
2. **门控仍是启发式**。`_may_need_tools()` 放宽了判据但仍非语义判断；极端表述
   （无数字、无查询动词的事实型问题）可能仍被拦下。真正的解法是取消门控、
   每次都让 LLM 判断，代价是每轮多一次 LLM 调用。
3. **选择增加一次 LLM 往返**（0.6–0.9s）。已用 `temperature=0.0` 保证确定性，
   但未做选择结果缓存。
4. **语料缺 3 条民法典条文**（340/342/966），需补充导入。
5. **`_auto_invoke_tools()` 成为死代码**，保留未删。
6. **工具无重试**。超时或失败即向模型报告失败，不重试。
7. **技能包填参较粗**：按 input_schema 把 query 填入所有必填字符串字段，
   未做字段语义区分。

---

## 六、项目进度

```
Phase 0: 配置对接与链路打通     [████████████████████] 100% ✅
Phase 1: 数据基础建设          [████████████████████] 100% ✅
Phase 2: 上下文管理+多轮对话    [████████████████████] 100% ✅
Phase 3: MCP+技能包深度集成     [████████████████████] 100% ✅
Phase 4: 商业化功能完善         [                    ]   0%
Phase 5: 安全合规+私有化部署    [                    ]   0%
```

---

## 七、部署提醒

后端无源码挂载，改代码后必须重建镜像：

```bash
docker-compose build backend
docker-compose up -d --force-recreate backend
```
