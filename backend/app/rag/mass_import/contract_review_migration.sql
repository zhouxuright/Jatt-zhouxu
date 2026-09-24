-- Migration: Create contract_reviews table for persisting contract review results.
-- Replaces the in-memory _reviews_store dict that was lost on restart.
-- Safe to run multiple times (uses IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS contract_reviews (
    id              VARCHAR(36)     NOT NULL PRIMARY KEY,
    user_id         VARCHAR(36)     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document_id     VARCHAR(36)     NULL REFERENCES documents(id) ON DELETE SET NULL,
    original_filename VARCHAR(512)  NOT NULL DEFAULT '',
    risk_score      DOUBLE PRECISION NULL,
    risk_items      TEXT            NULL,
    summary         TEXT            NULL,
    full_analysis   TEXT            NULL,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS ix_contract_reviews_user_id
    ON contract_reviews (user_id);

CREATE INDEX IF NOT EXISTS ix_contract_reviews_document_id
    ON contract_reviews (document_id);

CREATE INDEX IF NOT EXISTS ix_contract_reviews_created_at
    ON contract_reviews (created_at);
