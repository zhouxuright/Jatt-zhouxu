-- 法律数据全量导入 - 数据库迁移脚本
-- 为百万级数据优化索引和约束

-- =========================================================================
-- 1. 添加唯一约束（支持 ON CONFLICT DO NOTHING）
-- =========================================================================

-- laws 表：按 (name, effective_date) 唯一
-- 允许同名法律的不同版本共存，但同一版本不重复
DO $$
BEGIN
    ALTER TABLE laws ADD CONSTRAINT uq_law_name_date UNIQUE (name, effective_date);
EXCEPTION
    WHEN duplicate_table THEN NULL;
    WHEN others THEN NULL;
END $$;

-- legal_articles 表：按 (law_id, article_number) 唯一
-- 同一法律下的同一条文不重复
DO $$
BEGIN
    ALTER TABLE legal_articles ADD CONSTRAINT uq_law_article UNIQUE (law_id, article_number);
EXCEPTION
    WHEN duplicate_table THEN NULL;
    WHEN others THEN NULL;
END $$;

-- =========================================================================
-- 2. 优化索引
-- =========================================================================

-- 按法律类型索引（用于分类查询）
CREATE INDEX IF NOT EXISTS ix_laws_type ON laws(law_type);

-- 按法律状态索引（用于筛选有效法律）
CREATE INDEX IF NOT EXISTS ix_laws_status ON laws(status);

-- 按生效日期索引
CREATE INDEX IF NOT EXISTS ix_laws_effective_date ON laws(effective_date);

-- 条文按法律 ID 索引（已有，确保存在）
CREATE INDEX IF NOT EXISTS ix_articles_law_id ON legal_articles(law_id);

-- 部分索引：仅索引有效状态的条文（减少索引大小）
CREATE INDEX IF NOT EXISTS ix_articles_active ON legal_articles(law_id)
    WHERE effective_status = 'active';

-- 全文搜索索引（用于 PostgreSQL 全文检索，作为 Milvus 的补充）
-- CREATE INDEX IF NOT EXISTS ix_articles_content_fts ON legal_articles
--     USING gin(to_tsvector('simple', content));

-- =========================================================================
-- 3. 统计信息更新（导入后执行，帮助查询优化器）
-- =========================================================================

-- ANALYZE laws;
-- ANALYZE legal_articles;
