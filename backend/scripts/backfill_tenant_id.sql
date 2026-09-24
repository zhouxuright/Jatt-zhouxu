-- 存量行的 tenant_id 补齐（P0-2 多租户收尾）
--
-- 背景
-- ----
-- 迁移 008 只回填了当时的存量行。之后验证发现两处仍在产生 tenant_id 为空的
-- 新行，属代码缺陷而非历史遗留（已在同一批次修复）：
--
--   1. 对话创建：chat.py 的 3 处 Conversation(...) 未写入 tenant_id；
--   2. 审计中间件：audit_log.py 未从 JWT 取租户，且 JWT 本身也不含租户声明。
--
-- 代码已在同批次修复（JWT 增加 tid 声明 / 中间件透传 / 会话创建带租户）。
-- 本脚本负责把修复前产生的存量 NULL 行按"所属用户 → 用户租户"回填。
--
-- 注意
-- ----
-- 审计日志中约 4 成的行 user_id 为空（登录、注册等匿名请求）。这些请求本就
-- 不属于任何租户，NULL 是**正确**语义，不做回填，也不应被验证脚本判为失败。
--
-- 用法
-- ----
--   docker exec -i legal_postgres psql -U postgres -d legal_assistant \
--     -v ON_ERROR_STOP=1 < backend/scripts/backfill_tenant_id.sql

BEGIN;

-- 1) 会话：按会话所属用户回填
UPDATE conversations c
SET tenant_id = u.tenant_id::text
FROM users u
WHERE u.id::text = c.user_id
  AND c.tenant_id IS NULL;

-- 2) 审计日志：仅回填能解析出用户的那些行；匿名行保持 NULL
UPDATE audit_logs a
SET tenant_id = u.tenant_id::text
FROM users u
WHERE u.id::text = a.user_id
  AND a.tenant_id IS NULL;

-- 3) 兜底：用户已被删除、无法解析租户的会话，归入默认租户
--    （不硬编码 UUID，避免与 core/constants.py 漂移）
UPDATE conversations c
SET tenant_id = (SELECT id::text FROM tenants WHERE code = 'default' LIMIT 1)
WHERE c.tenant_id IS NULL
  AND c.user_id IS NOT NULL
  AND EXISTS (SELECT 1 FROM tenants WHERE code = 'default');

COMMIT;

-- 核查：应只剩匿名审计行（user_id IS NULL）为空
SELECT 'conversations' AS tbl, count(*) FILTER (WHERE tenant_id IS NULL) AS null_tenant
FROM conversations
UNION ALL
SELECT 'audit_logs(非匿名)', count(*) FROM audit_logs
 WHERE tenant_id IS NULL AND user_id IS NOT NULL
UNION ALL
SELECT 'audit_logs(匿名,预期保留)', count(*) FROM audit_logs
 WHERE tenant_id IS NULL AND user_id IS NULL;
