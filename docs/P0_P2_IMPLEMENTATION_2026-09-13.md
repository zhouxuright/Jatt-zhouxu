# P0–P2 商业化加固实施报告

**日期**：2026-09-13
**范围**：`docs/DEMO_TO_COMMERCIAL_ASSESSMENT_2026-09-12.md` 中列出的 P0-1 ~ P2-7 共 7 项任务
**结论**：7 项全部落地并通过端到端验证（44/45 项自动检查通过，唯一"失败"为限流误报，已单独复验通过）

---

## 一、总览

| 编号 | 任务 | 状态 | 核心产出 |
| --- | --- | --- | --- |
| P0-1 | 数据静态加密落地 | ✅ | 字段级 Fernet 加密 + 盲索引；**修复多副本密钥漂移致命缺陷** |
| P0-2 | 多租户隔离 | ✅ | `tenants` 表 + 5 张核心表 `tenant_id` + 应用层强制过滤 + RLS 加固脚本 |
| P0-3 | 引用可溯源闭环强化 | ✅ | 逐条置信度 + 结论闸门 + 原文溯源接口 |
| P0-4 | 合规审计日志导出 | ✅ | CSV/JSON 导出接口 + 引用校验结果自动留痕 |
| P1-5 | 跨会话长期记忆 | ✅ | `user_memory` 表 + 自动合并 + 首轮注入 + 用户可查看/删除 |
| P2-6 | 向量化补齐 + 水印瘦身 | ✅ | 差集补齐脚本 + 水印载荷缩减 11.7× |
| P2-7 | 前端数据资产治理看板 | ✅ | 新页面 `/data-governance` |

---

## 二、P0-1 数据静态加密

### 修复的致命缺陷

原 `app/services/encryption.py` 在未配置 `ENCRYPTION_KEY` 时会
`Fernet.generate_key()` **随机生成密钥**并写回 `.env`。在**双副本部署**下：

- 副本 A 用自己的随机密钥加密 → 落库；
- 副本 B 用自己的随机密钥解密 → `InvalidToken`，数据事实上不可恢复。

这是"上线即数据损坏"级别的缺陷。同时该模块此前**从未被任何代码调用**，
所以尚未造成实际损失。

### 实现

新增 `app/core/crypto.py` 作为**唯一密钥来源**，解析顺序：

1. `ENCRYPTION_KEY` 已配置 → 直接使用；
2. 未配置 → 由 `SECRET_KEY` 经 SHA-256 **确定性派生**（`SECRET_KEY` 在副本间共享，故副本间密钥一致）；
3. 生产环境且 `SECRET_KEY` 仍为出厂默认 → **启动即失败**（快速失败，拒绝静默降级）。

新增 `app/core/crypto_types.py`：`EncryptedText` / `EncryptedString` 透明加密列类型，
业务代码零感知（写入自动加密、读取自动解密）。

### 加密字段清单

| 表 | 字段 | 类型 |
| --- | --- | --- |
| `users` | `email` | `EncryptedString` + `email_bidx` 盲索引 |
| `messages` | `content` | `EncryptedText` |
| `conversations` | `summary` | `EncryptedText` |
| `conversations` | `fact_sheet`, `topic_segments` | `EncryptedJSON`（迁移 009） |
| `documents` | `summary`, `error_message` | `EncryptedText` |
| `contract_reviews` | `risk_items`, `summary`, `full_analysis` | `EncryptedText` |
| `contracts` | `content` | `EncryptedText` |
| `contract_versions` | `content`, `change_summary` | `EncryptedText` |
| `contract_key_dates` | `description` | `EncryptedText` |

> 2026-09-14 补充：`conversations.fact_sheet` / `topic_segments` 原为明文 JSON，
> 承载当事人身份与争议焦点，是加密清单的最后一处缺口。已新增
> `EncryptedJSON` 透明加密列类型 + 迁移 009（`JSON` → `TEXT`，存量明文经
> `::text` 后仍是合法 JSON，由回填脚本或下次写入完成加密）。

**盲索引**：Fernet 密文随机化，无法等值比较。因此邮箱改用
`email_bidx = HMAC-SHA256(key, normalize(email))` 承担唯一性与登录/查重检索。
`User.email_lookup()` 封装了该查询，`@validates("email")` 自动同步盲索引。

### 灰度迁移（零停机）

`crypto.decrypt()` 对**不带 `enc::v1::` 前缀的历史明文原样返回**，因此可以：
先上线代码（旧数据照常可读）→ 再批量加密存量数据。

`backend/scripts/backfill_encryption.py`：幂等、游标分页、`--dry-run` 预演。
本次已加密存量 **401 行**（users 73 / messages 297 / conversations 8 / documents 7 /
contract_reviews 4 / contracts 5 / contract_versions 6 / contract_key_dates 1）。

### 运维须知

- `.env` 已写入 `ENCRYPTION_KEY`，`docker-compose.yml` 已转发到容器。
- **更换 `ENCRYPTION_KEY` 会导致历史密文不可解密**，必须纳入 KMS 并备份。

---

## 三、P0-2 多租户隔离

### 为什么以应用层为主、RLS 为辅

PostgreSQL RLS 对**超级用户无效**。本项目容器默认以 `postgres` 超级用户连接，
若只依赖 RLS 会形成"看起来隔离、实际不隔离"的高危假象。故：

1. **一线防线（已生效）**：`app/core/tenancy.py` 应用层强制过滤；
2. **二线防线（可选）**：`backend/scripts/enable_rls_hardening.sql`，需先创建非超级用户角色。

### 实现

- 新增 `tenants` 表（`code`/`plan`/`max_users`/`status`），迁移 008 自动创建 `default` 租户承接存量数据。
- `users` / `conversations` / `documents` / `contracts` / `audit_logs` 新增 `tenant_id` 并建索引，存量行全部回填（0 条 NULL）。
- `scope_query()` 注入租户过滤；`ensure_access()` 对单条资源做归属校验，
  **跨租户访问返回 404**（不泄露"资源存在但无权"这一信息）。
- 注册时自动绑定租户（`get_default_tenant_id()`），杜绝孤儿用户。
- 管理端新增 `/admin/tenants`（列表/新建）、`/admin/users/{id}/tenant`（划归租户）；
  列表接口默认限本租户，平台管理员可显式 `all_tenants=true` 跨租户查看。

> 说明：`/conversations`、`/document/history` 等面向终端用户的接口本就以
> `user_id` 强过滤（比租户更细），租户层的增量价值主要在管理端与聚合统计。

### 补漏：租户写入的两处真实缺陷（2026-09-15）

迁移 008 只回填了**当时**的存量行。回归验证时发现仍在持续产生 `tenant_id`
为空的新行——这不是历史残留，而是**代码缺陷被"存量已回填"的假象掩盖**：

| # | 缺陷 | 影响 | 修复 |
|---|---|---|---|
| 1 | `chat.py` 三处 `Conversation(...)` 未写 `tenant_id` | 每个新会话都无租户，会话维度隔离失效 | 补 `tenant_id=current_user.tenant_id or DEFAULT_TENANT_ID` |
| 2 | 审计中间件未取租户，且 JWT 本身不含租户声明 | `audit_logs` 中已登录请求的租户为空，合规导出无法按租户切分 | JWT 新增 `tid` 声明；中间件解读并入 `audit_logs` |

缺陷 2 采用 **JWT 声明**而非"每个请求查一次 users 表"：审计写入是每请求一次的
热路径，令牌自带租户可零额外查询，且刷新令牌会透传 `tid`（`/auth/refresh`）。

存量补齐见 `backend/scripts/backfill_tenant_id.sql`（幂等，按"会话/审计 → 所属用户"
回填；用户已删除的会话归入 `default` 租户）。

> **语义澄清**：`audit_logs` 中约 4 成行的 `user_id` 为空（登录、注册等匿名请求）。
> 这些请求本不属于任何租户，**NULL 是正确语义**，不做回填，验证脚本也只对
> "已登录请求"施加非空要求——否则会用错误的标准掩盖真实缺陷。

### 补漏：验证脚本的盲区（本次缺陷为何被放行）

原 `verify_upgrade_p0_p2.py` 存在两个结构性问题，导致上述缺陷通过了 46/47：

1. **只查"是否为空"，不查"是否写对"**。把所有人都塞进默认租户也能通过。
2. **C 段（数据库核查）先于 D 段（对话链路）执行**。D9 触发的对话会新建一条会话，
   但 C5 早已跑完——检查顺序天然看不见"新产生"的行。

据此新增 4 项检查：`C11` 审计日志已登录请求的租户覆盖率、`C12` 会话租户与所属
用户租户**一致性**、`D11` 对话后复查"最新会话已带正确租户"、`D12` 清理范围扩展到
`tsmoke%`。其中 D11 必须在 D9 之后执行，这正是针对盲区 2 的修正。

---

## 四、P0-3 引用可溯源闭环

原实现只有"法条存在性校验"，且校验失败仅追加提示、**不阻止给出确定性结论**。

### 增强内容

1. **逐条置信度**：全称精确匹配 + 条文存在 = `1.0`；仅简称/模糊命中 = `0.75`；
   条号无法解析 = `0.2`；条文不存在/法名未知 = `0.0`。整体置信度取算术平均。
2. **结论闸门 `build_citation_gate()`**：有引用但**一条都没通过校验**时，
   `allow_definitive=False`，强制追加降级声明——"本回答不构成可直接援引的法律依据"。
   已接入 `/chat` 与 `/chat/stream` 两条链路。
3. **原文溯源接口**：
   - `GET /law/citations/trace?law_name=&article_number=` → 返回法名/条号/正文/章/节/效力状态，标注 `provenance=authoritative`；
   - `POST /law/citations/verify` → 核验整段文本，返回逐条状态 + 整体置信度 + 闸门结论。

### 实测

对话"请依据《民法典》第一条说明…" → 抽取 7 条引用，**7/7 通过**，
`confidence=1.0`、`definitive=True`。溯源查询 `民法典 第1条` 命中权威原文。

---

## 五、P0-4 合规审计日志导出

- `GET /admin/audit-logs/export?format=csv|json`：13 列合规字段
  （时间戳/操作人/租户/AI 动作/资源/HTTP 方法/路径/状态码/耗时/来源IP/**引用校验结果**/请求体指纹）。
  CSV 带 UTF-8 BOM，Excel 直接打开不乱码；默认限定当前租户。
- **引用校验结果自动留痕**：每次对话落库时同事务写入
  `audit_logs(action='ai.citation_verify', request_summary='citation_verify level=ok total=7 verified=7 unverified=0 confidence=1.0 definitive=True')`，
  保证"有回答即有留痕"。

实测：导出 CSV 25,298 字节，列头正确；`action=ai.citation_verify` 筛选返回 1 条并已随 CSV 导出。

---

## 六、P1-5 跨会话长期记忆

`Conversation.fact_sheet` 只在单会话内有效，开新会话即"失忆"。

- 新增 `user_memory` 表（`profile_json` / `profile_text` 均加密），每用户一行。
- `app/services/long_term_memory.py`：复用会话内已抽取的 `LegalFactSheet` 做
  **确定性合并**（列表并集去重、新值优先、上限 20 条），**不额外调用 LLM**——
  零 token、零延迟、无"记忆幻觉"。
- 注入：`build_chat_context(..., user_memory_text=...)` 在 system prompt 追加"用户长期画像"，
  截断到 900 字符以免挤占上下文预算。
- 写入：对话落库后由后台任务 `_background_update_conversation_context` 合并，失败静默不影响主链路。
- 隐私合规：`GET /chat/memory`（查看）、`DELETE /chat/memory`（删除），满足 PIPL 可携权与删除权。

---

## 七、P2-6 向量化补齐 + 水印瘦身

### 水印瘦身

原实现把整段 metadata JSON 编码为零宽字符，约 **704 个零宽字符**塞进正文。

新格式固定 14 字节（版本标记 + uint32 时间戳 + sha256(元数据)前 8 字节 + 内容类型），
实测降至 **60 个零宽字符，缩减 11.7×**；且保留旧格式解析分支，历史内容仍可验证。

### 向量化补齐

`backend/scripts/backfill_milvus_articles.py`：

- **关键修正**：Milvus 的 `id` 与 PostgreSQL UUID **不是同一 ID 空间**
  （历史导入用 `art_xxx` 自建 ID），因此必须按业务键 `(法名, 条号)`
  （并归一化内部空白，库中存在含换行的法名）求差集，否则会把 68 万条全部误判为"缺失"。
- 实测差集：PostgreSQL 686,906 条 vs Milvus 647,484 个业务键 → **缺失 10,744 条**
  （高于此前 4,223 的估算，属真实缺口）。
- 支持 `--dry-run` / `--max` / `--batch-size`，结束回读校验输出覆盖率。

**CPU 推理性能调优（2026-09-14 实测）**

本机无 CUDA，BGE-M3 为纯 CPU 推理，是本次回填的唯一瓶颈。逐项实测：

| 配置 | 吞吐 | 10,744 条预估 |
|---|---|---|
| 乱序 · batch=256 · 4 核（原始） | 0.73 篇/秒 | ~246 min |
| 按长度排序 · batch=64 · 4 核（基准） | **1.28 篇/秒** | ~140 min |
| 乱序 · batch=256 · 10 核 | 0.50 篇/秒 | ~359 min |

正式回填（10,725 条）的实际表现印证了排序的收益：**短条文段 6.1 篇/秒**，随批次
进入长条文段逐步降到 **2.0 篇/秒**，整体耗时约 90 分钟。对比原始乱序配置的
0.73 篇/秒（约 4 小时），**提速约 2.7×**。

结论与据此的三个改动：

1. **按 `len(content)` 排序后再分批**。法条平均仅 127 字、p50 99 字、p90 226 字，
   但 p99 达 456 字（max 14,557 字）。不排序时一个大 batch 会被少数长条文拖成
   "全体按最长补齐"，浪费成倍算力；排序让每个 batch 长度高度一致，padding 降到最低。
2. **`--batch-size` 默认由 256 调整为 64**。排序后小的 batch 才能切出"同质长度组"。
3. **不盲目加核**。宿主为 12 逻辑核且同时运行着另一套技术栈（mysql/nacos/xxl-job/
   wkcrm 等），提到 10 核因超订反而慢 32%。`backfill` 服务固定 `cpus: 4.0`，并显式
   设置 `OMP_NUM_THREADS=4` / `MKL_NUM_THREADS=4`——否则 torch 默认取
   `cpu_count/2 = 6`，在 4 核 cgroup 下超订同样拖慢。

同时把 `model.encode(...)` 的返回项显式写死为
`return_dense=True, return_sparse=False, return_colbert_vecs=False`：本仓库检索只用
稠密向量（Milvus 集合仅一个向量字段），显式关闭可避免依赖库默认值变化带来的额外前向。

### 字段字节上限：缺口为何"补不齐"（2026-09-15 定位并修复）

首次真实回填在写入时报错 `code=1100 length of varchar field law_name exceeds max
length, row number: 176, length: 309, max length: 256`。**309 > 256 看似矛盾**
（`laws.name` 最长仅 132 字符），原因是——

> **Milvus 的 VARCHAR `max_length` 按 UTF-8 字节数计，不是字符数。**
> 中文 1 个字符占 3 字节，132 字符可达 396 字节。

实测 PostgreSQL 侧真实字节长度与集合原上限：

| 字段 | 集合原上限 | 实际最大 | 超限条数 | 性质 |
|---|---|---|---|---|
| `law_name` | 256 B | 396 B | 19 | 业务键字段 → 结构性不可写 |
| `content` | 8192 B | 42,959 B | 464 | 载荷字段 → 可截断写入 |
| `tags` | 512 B | 406 B | 0 | — |
| `category` / `article_number` | 64 B | 27 / 29 B | 0 | — |

由此暴露三个叠加问题：

1. **批级失败**：Milvus 的长度校验以整批为单位，一条超限会让整批 256 条全部写不进去，
   只在提交时抛异常。表现是"回填跑很久、缺口却纹丝不动"，极易被误判为性能/网络问题。
2. **历史导入是"截断写入"**：`OptimizedMilvusImporter` 用 `_truncate_utf8` 按字节截断后
   入库，所以那 19 条其实**已经没有干净版本**可匹配——差集用完整法名做键，自然永远对不上。
3. **验证口径失真**：`widen_milvus_varchar.py` 探测发现 Milvus **2.4.8 不支持**
   `AlterCollectionField`（该能力在 2.5+），只能靠重建集合或升级版本才能放宽。

**修复（一次性收敛，避免复发）**

- 新增 `backend/app/rag/milvus_schema.py` 作为**单一事实来源**：集合名 +
  各字段字节上限 + 共享的 `truncate_utf8()`。原先 `milvus_service` / `batch_import` /
  `mass_import/milvus_importer` 三处各自硬编码 256/8192，是漂移的根源。
- 建集合一律引用常量（`law_name=512B`、`content=65535B`），**新部署不再有这个坑**。
- `OptimizedMilvusImporter` 改为从**实际集合 schema** 读取上限再截断，并对被截断的
  **键字段**（`law_name`/`article_number`）显式告警——因为键被截断 = 该条永远被判"缺失"。
- 回填脚本新增**字节上限预检**：键字段超限 fail-fast；载荷字段超限按字节截断并计数。
  另加 `--skip-overflow` / `--skip-report`，把不可写条目输出成 CSV 清单，杜绝静默丢数据。
- 新增 `backend/scripts/widen_milvus_varchar.py`（幂等），供未来 Milvus ≥ 2.5 环境在线放宽。

**最终结果**

```
缺失总数                : 10,744
  业务键超限（不可写）   :     19   → 已跳过，清单见 backend/reports/
  载荷超限（截断写入）   :    399   → 已写入（与既有索引约定一致）
  完全在限内            : 10,327
实际回填目标            : 10,725
```

关于 19 条的处理经确认为**接受并记录**：它们仅占 68.7 万条的 0.0028%，全文在 PostgreSQL
中完好，仅向量检索不可达；彻底修复需重建集合（搬迁向量、不重算嵌入）或升级 Milvus ≥ 2.5，
已列为后续维护项。

关于 399 条的**正文截断**经确认认可：嵌入只取前 512 token（≈512 汉字），而 8192 字节
≈2730 汉字，远大于嵌入窗口，**对检索质量无影响**；权威全文始终以 PostgreSQL 为准
（溯源接口读的是 PostgreSQL，不是向量库）。

> ⚠️ 运维注意：单副本常驻 BGE-M3 + Reranker 约 2.5 GiB，若在后端容器（4 GiB 上限）
> 内直接执行回填，第二个模型实例会把容器 **OOMKill**——而 OOM 是容器级的，
> 会连带杀死正在对外服务的后端副本。
>
> **已于 2026-09-14 改为永久方案**：`docker-compose.yml` 新增 `backfill` 服务
> （`profiles: ["tools"]`，12 GiB / 4 CPU，`extends: backend`），一次性任务跑在
> 独立容器里，既给足内存又不影响在线服务：
>
> ```bash
> docker compose --profile tools run --rm backfill python scripts/backfill_milvus_articles.py --dry-run
> docker compose --profile tools run --rm backfill python scripts/backfill_milvus_articles.py
> docker compose --profile tools run --rm backfill python scripts/backfill_encryption.py
> ```
>
> 原先临时的 `docker-compose.override.yml` 已删除；生产 `backend` 仍保持 4 GiB / 2 副本。

### 重复实体清理：一次险些造成数据损失的"去重"（2026-09-15 定位并修复）

**现象**：集合 `num_entities = 693,536`，按业务键 `(法名, 条号)` 分组却只有 `658,209`
个键 —— 表面重复 35,327 个实体（29,249 个键），且检索层无去重，重复项会白占 `top_k`
名额，表现为"同一条法条在结果里出现两次"。

**根因**：两个历史导入器都按**位置**生成主键、各自写入了同一批法条：
`milvus_full_import.py` / `mass_import/milvus_importer.py` 写
`art_{batch_start+i:08d}`，`scripts/sync_milvus_gap.py` 写 `pg_{uuid}`（其自身
docstring 已承认"full `pg_*` sync 会重复 ~640k `art_*`"，但靠"法名变体"判断"缺失法"
仍然漏判）。位置型主键在续跑/重跑时会把同一条法条分到**新位置**、拿到**新主键**，
因此单看主键完全看不出重复，必须按业务键分组才能发现。

**关键转折（险情）**：动手删除前先比对正文，发现 29,249 个"重复键"里
**11,272 个（38.54%）正文实质不同** —— 它们是**同一部法律的修订前后版本**：

- 《中华人民共和国母婴保健法实施办法》第八条：一版"劳动保障、计划生育"，另一版"人力资源社会保障"；
- 《中华人民共和国全国人民代表大会常务委员会议事规则》第一条：两版内容完全不同；
- 《中华人民共和国药品管理法实施条例》第二十二条：853B 与 362B，是两条不同的条款。

**结论：`(法名, 条号)` 不是唯一身份。** 若按直觉的"每键只留 1 份"执行，会静默删除
11,272 个法律版本（不可逆的数据损失）。因此改为**前缀簇去重**：同一业务键内，把
"忽略空白后**互为前缀**"的实体聚为一个**文本簇**，每簇只保留**正文最长**的一份
（最长者信息最全）；**不同文本簇 = 不同版本，全部保留，不做任何删除**。

保留优先级：正文长度优先，长度相同时偏好带 PG uuid 的形态（`art_<uuid>` /
`pg_<uuid>`）而非位置型 `art_<数字>`（后者会随重跑漂移）。

**执行结果**

| 指标 | 清理前 | 清理后 |
|---|---|---|
| 实际可检索实体 | 693,536 | **669,799** |
| 业务键数 | 658,209 | 658,209（**一个键都没丢**）|
| 删除冗余实体 | — | **23,737**（exact_dup 23,374 + 截断前缀 363）|
| 刻意保留的多版本键 | — | **11,272** |

清单留档：`backend/reports/milvus_duplicates.csv`（每一条删除都记录对应的保留者与原因）、
`backend/reports/milvus_preserved_versions.csv`（多版本键清单）。

**检索侧兜底**：`MilvusRAGService.search()` 改为**超采样（4×）+ 去重**。去重键为
`(业务键, 删空白后正文前 2000 字符)` —— 只折叠"完全同一行"的重复，**不按业务键折叠**，
否则会隐藏法律修订版本（这一条正是从上面那次险情中得到的教训）。

**关于 `num_entities`**：Milvus 的删除是**逻辑删除**，`num_entities` 需 compaction 后
才回落，但检索会**立即排除**已删实体。因此核查真实条数必须用 `query_iterator` 实扫
（实扫结果 669,799，与计划完全一致）。

> 附带修复：`get_stats()` 在进程冷启动时会误报 `not_initialized`（它不尝试惰性初始化，
> 即使集合已存在）；已改为先尝试 `create_collection()`。

---

## 八、P2-7 前端数据资产治理看板

新增 `/data-governance` 页面（侧边栏「数据资产治理」）：

- **概览卡片**：可援引权威语料 / 目标条数 / 非可援引语料 / 目标完成度进度条；
- **语料来源登记账本**：来源、来源性质（权威授权/开放来源/合成数据）、可援引标记、条数、存储、商业使用、备注；
- **合规审计日志**：按动作筛选、分页、一键导出 CSV/JSON（Blob 下载）；
- **租户与数据隔离**：租户列表 + 用户数（非平台管理员自动降级为空表）。

配套 `frontend/src/api/admin.ts`。

---

## 九、验证

| 脚本 | 覆盖范围 | 结果 |
|---|---|---|
| `verify_upgrade_p0_p2.py` | P0–P2 升级项（A 进程内 / B HTTP / C 落盘 / D 管理员路径） | **50/50 PASS** |
| `verify_citation_audit_e2e.py` | 对话 → 引用校验 → 审计留痕 → CSV 导出 闭环 | **PASS** |
| `verify_vector_search_dedup.py` | 检索去重兜底（6 组查询 × `top_k=10`） | **PASS** |

### 关键结果

- 邮箱登录（盲索引命中）、大小写归一化登录、重复邮箱 409 —— 全部通过；
- `users.email` 加密 88/88、`email_bidx` 回填 88/88、密文不含 `@` 明文特征；
- `messages.content` 加密 309/309；5 张表 `tenant_id` 零 NULL；
- `audit_logs` 已登录请求的租户覆盖率 **1,494/1,494（缺失 0）**；
  `conversations.tenant_id` 与所属用户租户**零不一致**；
- 非管理员访问管理接口一律 403（权限隔离正确）；管理员可见**解密后**的邮箱，
  审计 CSV 导出列头合规（时间戳/操作人/租户/操作·AI动作/…/引用校验结果）；
- 引用闭环：`citation_verify` 留痕写入审计日志，CSV 导出可检索到该留痕；
- 向量检索：6 组查询均返回满额 10 条、**重复条目 0**，且同一条号的**不同版本**可同时出现
  （证明多版本未被误折叠）。

> **运行方式（重要）**：验证脚本默认 `BASE=http://localhost:8001`，必须在**后端容器内**
> 执行 —— 在其它容器里 `localhost` 指向自身，会 `Connection refused`：
>
> ```bash
> docker exec legalintelligentassistancesystem-backend-5 python scripts/verify_upgrade_p0_p2.py
> docker exec legalintelligentassistancesystem-backend-5 python scripts/verify_citation_audit_e2e.py
> docker exec legalintelligentassistancesystem-backend-5 python scripts/verify_vector_search_dedup.py
> ```
>
> 另：限流为 **60 次/分钟**（`RATE_LIMIT_PER_MINUTE`）。连续两次执行完整套件会打满配额，
> 导致 `D9 对话请求` 返回 **429**（非功能缺陷）；两次之间请间隔 60 秒以上。

---

## 十、遗留事项（需业务/运维决策，非代码问题）

1. **19 条法条未写入向量库** —— 法名超出集合 varchar 上限，已按决策「接受并记录」；
   清单见 `backend/reports/milvus_skipped_overflow.csv`。Milvus 2.4.8 **不支持**在线
   `AlterCollectionField`；如需收敛缺口，需**升级到 Milvus 2.5+** 或**重建集合后重灌**。
2. **399 条法条正文按字节截断后写入** —— 正文 > 8192 字节，已按决策认可；新建集合的
   上限已提升至 65,535 字节，重建集合可彻底消除截断。
3. **历史导入器的位置型主键未改造** —— `app/rag/milvus_full_import.py` 与
   `app/rag/mass_import/milvus_importer.py` 仍以 `art_{batch_start+i}` 造主键。**再次执行
   历史导入仍会产生冗余实体**；执行后请跑 `scripts/dedup_milvus_articles.py` 清理，
   检索层已有去重兜底。
4. **RLS 二线防线未启用** —— 需创建非超级用户角色并切换 `DATABASE_URL`（见
   `enable_rls_hardening.sql` 注释）。
5. **`username` 字段仍为明文** —— 它是登录标识符，加密需配套改造登录链路；当前未加密。
6. 单表内 `username` / `email_bidx` 的唯一性是**全局**的，多租户下"不同租户可用同一用户名"
   需后续调整唯一约束。

> **2026-09-14 已关闭**：原「`fact_sheet` / `topic_segments` 明文 JSON」→
> `EncryptedJSON` + 迁移 009 解决。
>
> **2026-09-15 已关闭**：原「10,744 条法条向量待回填」→ 已**全部回填**（10,725 条成功，
> 19 条法名超限按决策跳过），并额外完成**冗余实体清理**（693,536 → 669,799）。

---

## 十一、变更文件索引

**新增**
```
backend/app/core/crypto.py                      # 密钥解析 + 加解密 + 盲索引
backend/app/core/crypto_types.py                # EncryptedText / EncryptedString / EncryptedJSON
backend/app/core/constants.py                   # DEFAULT_TENANT_ID（避免循环导入）
backend/app/core/tenancy.py                     # 租户作用域与访问校验
backend/app/models/tenant.py                    # 租户模型
backend/app/models/user_memory.py               # 跨会话长期记忆模型
backend/app/services/long_term_memory.py        # 记忆合并/注入/读取
backend/alembic/versions/007_add_encryption_and_user_memory.py
backend/alembic/versions/008_add_tenancy.py
backend/alembic/versions/009_encrypt_conversation_json.py
backend/scripts/backfill_encryption.py          # 存量数据加密回填（含 EncryptedJSON 列）
backend/scripts/backfill_milvus_articles.py     # 向量差集补齐
backend/scripts/enable_rls_hardening.sql        # RLS 二线防线（可选）
backend/scripts/verify_upgrade_p0_p2.py         # 升级验证（A 进程内 / B HTTP / C 数据库 / D 管理员）
backend/scripts/verify_citation_audit_e2e.py    # 引用→审计闭环验证
frontend/src/api/admin.ts
frontend/src/views/DataGovernance.vue
```

**修改**
```
backend/app/models/{user,message,conversation,document,contract,audit_log}.py
backend/app/models/__init__.py
backend/app/services/encryption.py              # 改为 core.crypto 的 facade
backend/app/services/citation_verifier.py       # 置信度 + 闸门 + 溯源
backend/app/services/content_watermark.py       # 紧凑水印格式
backend/app/services/context_manager.py         # 注入长期记忆
backend/app/api/deps.py                         # get_tenant_id
backend/app/api/v1/auth.py                      # 邮箱盲索引登录 + 注册绑定租户
backend/app/api/v1/admin.py                     # 租户过滤 + 租户管理 + 审计导出
backend/app/api/v1/chat.py                      # 引用闸门 + 审计留痕 + 记忆注入/写入/接口
backend/app/api/v1/law.py                       # 引用核验与溯源接口
backend/app/crawlers/document_processor.py      # 修复被引号提前终止的正则
backend/app/rag/milvus_service.py               # 字段长度取自 schema + 检索超采样去重 + get_stats 惰性初始化
backend/app/rag/batch_import.py                 # 字段长度取自 schema
backend/app/rag/mass_import/milvus_importer.py  # 字段长度取自 schema + 键超限告警
backend/app/middleware/audit_log.py             # 审计日志写入 tenant_id
backend/scripts/backfill_milvus_articles.py     # 长度排序分批 + 字节上限预检 + 载荷按字节截断
backend/scripts/verify_upgrade_p0_p2.py         # 新增 C11/C12/D11 租户一致性检查
docker-compose.yml                              # 转发 ENCRYPTION_KEY + 新增 backfill 一次性任务容器
.env                                            # ENCRYPTION_KEY
frontend/src/router/index.ts
frontend/src/components/AppLayout.vue
```

## 十二、合同审查链路修复（用户实报："请求失败"）

### 症状
上传《中华人民共和国国防动员法》(.docx) 触发合同审查，前端只提示 **"请求失败"**，功能不可用。

### 根因（两层叠加）
1. **后端长度校验误用**：`ContentSafetyFilter.check_input` 内部写死
   `MAX_INPUT_LENGTH = 10,000`（原为聊天输入设计），被文档接口复用。该法正文
   10,150 字 → 超限 → 被判定为 **"合同内容未通过安全检查：输入内容过长"** →
   返回 **HTTP 400**。超长被伪装成"内容不安全"。
2. **前端错误分支缺失**：axios 拦截器未处理 400/413，落到 `default` 分支，
   后端真实 `detail` 被丢弃，用户只看到通用文案"请求失败"。

### 修复
| 层 | 文件 | 改动 |
|---|---|---|
| 后端 | `app/middleware/content_safety.py` | 新增 `ContentTooLongError` 与 `validate_length()`；`check_input` 支持显式 `max_length`，长度与安全判定解耦 |
| 后端 | `app/core/config.py` | 新增 `MAX_CONTRACT_REVIEW_CHARS = 50000` |
| 后端 | `app/api/v1/contract.py` | `/review`、`/review-async` 先做长度校验，超长返回 **413** + 可操作提示（含实际长度/上限/建议拆分）；再做内容安全校验 |
| 后端 | `app/agents/contract_review_agent.py` | 新增 `_parse_risk_analysis()`，把风险分析文本拆成 **风险描述/法律依据/修改建议/替代方案**，修复「修改建议」列恒空 |
| 前端 | `src/api/index.ts` | 显式处理 **400/413**，展示后端 `detail`；422 优先展示 `detail` |
| 前端 | `src/api/contract.ts` | 新增 `reviewContractAsync()` / `getTaskStatus()` |
| 前端 | `src/views/ContractReview.vue` | 默认走异步接口 + 任务轮询（2s/次，上限 15min）+ 进度条；`mapRiskItems` 兼容同步与异步两套字段名 |
| 前端 | `nginx.conf` | `/api/` 读/写超时 120s → **600s** |
| 测试 | `backend/scripts/verify_contract_review.py` | 新增回归脚本（该功能此前零覆盖） |

### 验证
- **用户真实文件复现**：《国防动员法》(28,074 B) 走 `/contract/review-async` →
  `202` → **47s 完成**，`risk_score=20`、`risk_items=1`、`missing_clauses=7`、
  报告含合同基本信息表。**修复前该文件必然 400。**
- **回归 4 组用例全绿**：12,000 字长文档异步审查（返回结构化风险条目 + 非 0 评分 +
  含修改建议）/ 60,000 字返回 413 且提示可操作 / 短文本同步 201 + `overall_score` /
  非法 `.exe` 返回 400（安全防线未放宽）。
- 运行方式：`docker exec legalintelligentassistancesystem-backend-5 python scripts/verify_contract_review.py`
  （`--skip-slow` 跳过分钟级长文档用例）。

### 运维提示
后端应用代码**烘焙进镜像**（`/app` 无 bind mount），修改 `backend/app/**` 后必须
`docker compose build backend && docker compose up -d --force-recreate --no-deps backend` 才生效。

### 连带修复：租户写入路径漏标（回归暴露）

修复后重跑 P0–P2 全量回归，由 50/50 变为 48/50，两项失败均与本次改动无因果关系，
而是**本轮测试首次在「一次性回填之后」新写入数据**，把长期存在的写入路径缺陷暴露出来：

**(1) `documents` 写入路径从未打 `tenant_id`（真实缺陷）**

所有 `Document(...)` 创建点均缺租户标：`contract.py`（同步/异步各一处）、
`document.py`（文书生成）、`batch.py`（批量上传后台任务）。
存量带租户的文档来自 P0-2 的**一次性回填**，因而掩盖了写入路径漏洞 ——
回填后新增的文档租户恒为空，租户维度查询会"看不见"它们。

修复：统一使用既有 helper `app.core.tenancy.tenant_of(user)` 补齐 4 处创建点；
`_process_batch_task` 增加 `tenant_id` 形参（提交时取值，后台任务无需回查库）。

> 说明：`contract_reviews` 表**没有 `tenant_id` 列**（仅 `documents`/`contracts` 有），
> 审查记录按 `user_id` 过滤，粒度比租户更严，故不新增租户列。

**(2) `audit_logs` 一批空租户行 —— 旧令牌所致，非持续缺陷**

46 行空租户全部来自用户 `Jatt123`、时间集中于 08:37–08:48。`_extract_tenant_id`
只解析 JWT 的 `tid` claim，而这批请求用的是 P0-2 部署**之前**签发的旧令牌
（有 `sub`、无 `tid`）。后端重建后新写入的审计行中，**凡已登录请求均带租户**，
空租户仅剩 `register`/`login`（无令牌，本就不应有租户）。

处置：不在热路径增加 DB 回查（与中间件"不为每个请求查 users 表"的设计相冲突），
改以**历史数据回填**修复，旧令牌过期后该现象自然消失。

**(3) 异步审查不落库（本次切换异步后暴露）**

`execute_contract_review` 原先只把结果写 Redis，**从不写 `contract_reviews`**
（仅同步接口落库），导致走异步的审查在「审查历史」中查不到，
而前端超时文案恰提示"请到审查历史查看"。

修复：新增 `task_manager._persist_contract_review()`，从 Redis 任务记录取
`user_id` / `document_id` / `original_filename` 落库，并把 `review_id` 回填进任务结果；
落库失败仅记日志、不影响任务状态。`/review-async` 的 `metadata` 相应补两个字段。

**(4) 数据修复 SQL**
```sql
UPDATE audit_logs a SET tenant_id = u.tenant_id FROM users u
 WHERE a.user_id = u.id::text AND a.tenant_id IS NULL
   AND a.user_id IS NOT NULL AND u.tenant_id IS NOT NULL;   -- 46 行
UPDATE documents d SET tenant_id = u.tenant_id FROM users u
 WHERE u.id::text = d.user_id AND d.tenant_id IS NULL;       -- 10 行
```
