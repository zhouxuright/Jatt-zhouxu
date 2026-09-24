-- ============================================================================
-- 多租户 RLS 二线防线（可选加固，P0-2）
-- ----------------------------------------------------------------------------
-- 前置条件（重要）：
--   PostgreSQL 的 RLS 对 **超级用户无效**。本项目容器默认以 ``postgres``
--   超级用户连接，因此必须先创建一个非超级用户的应用角色，并把
--   DATABASE_URL 切换到该角色，RLS 才会真正生效。
--
-- 使用步骤：
--   1) 修改下方 :app_password 为强口令后执行本脚本；
--   2) 更新 .env：DATABASE_URL=postgresql+asyncpg://legal_app:<pwd>@postgres:5432/legal_assistant
--   3) docker compose up -d --no-deps backend
--   4) 验证：以 A 租户身份访问 B 租户数据，应返回 0 行 / 404。
--
-- 应用层过滤（app/core/tenancy.py）是一线防线，本脚本是纵深防御的第二层：
-- 即使应用层出现编码疏漏，数据库仍会兜底拦截。
-- ============================================================================

-- 1. 创建非超级用户应用角色
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'legal_app') THEN
        CREATE ROLE legal_app LOGIN PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
    END IF;
END $$;

-- 2. 基础权限
GRANT CONNECT ON DATABASE legal_assistant TO legal_app;
GRANT USAGE ON SCHEMA public TO legal_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO legal_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO legal_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO legal_app;

-- 3. 启用行级安全策略
--    app.tenant_id 由应用在会话内通过 SET LOCAL 注入（见 db_session_hook）。
DO $$
DECLARE
    t text;
    tenant_tables text[] := ARRAY['users', 'conversations', 'documents', 'contracts', 'audit_logs'];
BEGIN
    FOREACH t IN ARRAY tenant_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        -- FORCE 让表属主也受策略约束（否则属主可绕过）
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);

        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format($f$
            CREATE POLICY tenant_isolation ON %I
            USING (
                tenant_id IS NULL
                OR tenant_id = current_setting('app.tenant_id', true)
            )
            WITH CHECK (
                tenant_id IS NULL
                OR tenant_id = current_setting('app.tenant_id', true)
            )
        $f$, t);
    END LOOP;
END $$;

-- 4. 验证（以 legal_app 身份执行）
--    SET LOCAL app.tenant_id = '00000000-0000-0000-0000-000000000001';
--    SELECT count(*) FROM conversations;   -- 只应看到该租户的数据

COMMENT ON POLICY tenant_isolation ON users IS
    'P0-2 多租户隔离：仅允许访问当前会话租户的数据';
