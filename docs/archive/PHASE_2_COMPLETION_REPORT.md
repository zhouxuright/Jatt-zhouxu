# Phase 2 完成报告：上下文管理与多轮对话优化

**完成时间**: 2026-09-03
**状态**: ✅ 已完成并验证

---

## 一、交付内容

### 1. 新增：三层上下文管理器

**文件**: `backend/app/services/context_manager.py`

| 层级 | 作用 | 配置 |
|------|------|------|
| 短期记忆 | 最近N轮原文滑动窗口 | `MAX_RECENT_TURNS = 10` |
| 长期记忆 | 超出窗口的历史LLM摘要压缩 | `MAX_SUMMARY_TOKENS = 1000`，阈值 `COMPRESSION_THRESHOLD = 15` |
| 工作记忆 | RAG检索的法条上下文 | `MAX_RAG_TOKENS = 3000` |

核心方法：
- `build_context()` — 组装三层上下文，返回token预算元数据
- `_get_recent_messages()` — 滑动窗口加载
- `_get_or_create_summary()` — 摘要复用或生成
- `_generate_summary()` — LLM压缩，失败时降级到 `_fallback_summary()`
- `build_chat_context()` — 与Chat API的集成入口

### 2. 修改：Chat API 接入上下文管理

**文件**: `backend/app/api/v1/chat.py`

- 新增 `_build_context_with_manager()`，失败时自动降级到原 `_get_conversation_history()`
- 三个端点接入：`/chat/stream`、`/chat`、`/chat/voice`
- 新增 `GET /conversations/{id}/stats` 监控端点

### 3. 修复：三个真实缺陷

#### 缺陷1 — 摘要生成提前提交事务
`_get_or_create_summary()` 内的 `await self.db.commit()` 在助手消息保存前结束了请求事务。
**修复**：改为仅在ORM对象上暂存 `conversation.summary`，事务边界交还调用方。

#### 缺陷2 — 路由注册顺序导致 stats 端点 404
`/conversations/{conversation_id}` 先注册，其路径参数吞掉了 `/stats` 后缀。
**修复**：将 stats 路由移到通用路由之前。

#### 缺陷3 — SSE生成器中助手消息静默丢失（关键）
**现象**：客户端收到完整回复，但数据库中缺少该条助手消息。18轮测试中5轮丢失3轮。

**根因**：`BaseHTTPMiddleware` 在响应完成时销毁其 anyio cancel scope，向生成器抛出 `CancelledError`。该异常继承自 `BaseException` 而非 `Exception`，因此 `except Exception` 完全捕获不到，写入失败且无任何日志。

**修复**（两处）：
1. 将写入移到最后一个 `done` 事件 yield **之前**，使其运行在仍活跃的任务作用域内
2. 提取 `_persist_assistant_message()` 辅助函数，使用独立的 `async_session_factory()` 会话而非请求作用域的 `db`
3. 生成器运行前快照 `conversation_id_str` / `conversation_needs_title` 为普通值，避免 ORM 对象脱离会话
4. 相同缺陷也存在于语义缓存命中路径（`cached_event_generator`），同步修复

---

## 二、验证结果

### 测试1：多轮上下文延续（`test_phase2_context.py`）

5轮对话，含代词指代和历史事实回溯：

| 轮次 | 输入 | 消息数 | 上下文验证 |
|------|------|--------|-----------|
| 1 | 工作3年，月薪1万，被辞退赔偿多少？ | 2 | — |
| 2 | 如果公司不给我赔偿呢？ | 4 | ✅ "赔偿"正确指代第1轮 |
| 3 | 那我刚才说的工作年限，赔偿金怎么算？ | 6 | ✅ 回溯到第1轮的3年工龄 |
| 4 | 仲裁需要准备哪些材料？ | 8 | ✅ 承接第3轮 |
| 5 | 这个流程大概要多久？ | 10 | ✅ "这个"正确指代第4轮的仲裁 |

消息数序列 **2, 4, 6, 8, 10** — 每轮完整持久化用户+助手两条。
数据库确认：`assistant=5, user=5`。

### 测试2：摘要压缩（`test_phase2_summary.py`）

18轮对话，验证阈值触发与跨窗口召回：

- 第1–15轮：`summary=False`（未达阈值）
- **第16轮：`summary=True, len=167`** — 恰好在 `message_count > 30` 时触发
- 第16–18轮：摘要稳定复用，未重复生成
- 消息数 2→36，18轮全部正确持久化

**跨窗口召回验证**：第16轮提问"我最开始问的那个辞退赔偿，结论是什么？"——第1轮的原始消息此时已滑出10轮窗口，AI仍准确回答"工作3年、月薪1万元"，证明信息确实通过摘要传递。

**生成的摘要**（167字符，压缩自30条消息）：
> 用户咨询公司违法辞退赔偿问题，工作3年月薪1万。助手建议先厘清解除理由，区分经济补偿金（N）与违法解除赔偿金（2N），依据《劳动合同法》第47条和第87条计算。若公司拒付，可申请劳动仲裁，需准备劳动合同、工资流水、解除通知等材料，依据《劳动争议调解仲裁法》第28条。仲裁流程一般45日内审结，费用免费。结论：建议收集证据启动仲裁程序。

摘要保留了关键事实、法条编号和结论，符合设计意图。

---

## 三、部署注意事项

**后端无源码挂载**：`docker-compose.yml` 的 backend 服务只挂载了 `backend_uploads` 和 `hf_cache`，源码在构建时打入镜像。修改后端代码后 `docker-compose restart backend` **不会生效**，必须：

```bash
docker-compose build backend
docker-compose up -d --force-recreate backend
```

验证代码已进入容器：
```bash
docker exec legalintelligentassistancesystem-backend-1 \
  sh -c 'grep -c "_persist_assistant_message" /app/app/api/v1/chat.py'
```

---

## 四、未完成/已知限制

1. **Token估算为启发式**：`_estimate_tokens()` 按1.5字符/token估算中文，非真实分词器计数。`LLMService.count_tokens()` 同样是启发式且仍未被上下文层调用。
2. **无上下文溢出重试**：若组装后的上下文超出模型窗口，LLM调用直接失败，没有降级重试逻辑。
3. **无按模型的窗口配置**：DeepSeek/GPT-4o/Qwen/GLM 窗口大小不同，当前未做区分。
4. **语义缓存无上下文感知**：相同问题在不同对话上下文中会命中同一缓存条目。
5. **摘要不增量更新**：一旦生成便复用，后续新消息滑出窗口时不会刷新摘要。
6. **`_get_conversation_history()` 仍保留**：作为降级路径，未删除。

---

## 五、项目进度

```
Phase 0: 配置对接与链路打通     [████████████████████] 100% ✅
Phase 1: 数据基础建设          [████████████████████] 100% ✅
Phase 2: 上下文管理+多轮对话    [████████████████████] 100% ✅
Phase 3: MCP+技能包深度集成     [                    ]   0%
Phase 4: 商业化功能完善         [                    ]   0%
Phase 5: 安全合规+私有化部署    [                    ]   0%
```
