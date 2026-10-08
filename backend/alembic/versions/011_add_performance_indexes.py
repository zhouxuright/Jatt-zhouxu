"""Add performance indexes for analytics queries.

Creates composite indexes on frequently queried columns:
- conversations(tenant_id, created_at) — tenant-scoped time-range queries
- conversations(user_id, created_at) — user-scoped time-range queries
- messages(conversation_id, created_at) — conversation message ordering
- documents(upload_time) — document listing/sorting

Revision ID: 011
Revises: 010
Create Date: 2026-10-08
"""

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite indexes for conversations (analytics dashboard queries)
    op.create_index(
        "ix_conversations_tenant_created",
        "conversations",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_conversations_user_created",
        "conversations",
        ["user_id", "created_at"],
    )

    # Composite index for messages (time-range within conversation)
    op.create_index(
        "ix_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at"],
    )

    # Index on documents.upload_time (was missing)
    op.create_index(
        "ix_documents_upload_time",
        "documents",
        ["upload_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_upload_time", table_name="documents")
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_index("ix_conversations_user_created", table_name="conversations")
    op.drop_index("ix_conversations_tenant_created", table_name="conversations")
