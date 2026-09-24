"""Add compliance tracking tables: watchlists, regulation_changes, compliance_alerts

Revision ID: 006
Revises: 005
Create Date: 2026-09-11

P1.5 compliance risk dynamic tracking: persistent regulation-change monitoring
with per-user watchlists and risk alerts.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'compliance_watchlists',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('industry', sa.String(64), nullable=False, server_default=''),
        sa.Column('topics', sa.Text(), nullable=True),
        sa.Column('compliance_domains', sa.Text(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_scan_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_compliance_watchlists_user_id', 'compliance_watchlists', ['user_id'])

    op.create_table(
        'regulation_changes',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('dedupe_key', sa.String(256), nullable=False),
        sa.Column('title', sa.String(512), nullable=False),
        sa.Column('law_type', sa.String(64), nullable=False, server_default=''),
        sa.Column('publish_date', sa.String(32), nullable=False, server_default=''),
        sa.Column('effective_date', sa.String(32), nullable=False, server_default=''),
        sa.Column('status', sa.String(32), nullable=False, server_default=''),
        sa.Column('source', sa.String(64), nullable=False, server_default='flk'),
        sa.Column('source_id', sa.String(128), nullable=False, server_default=''),
        sa.Column('url', sa.String(1024), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dedupe_key', name='uq_regulation_changes_dedupe'),
    )
    op.create_index('ix_regulation_changes_publish_date', 'regulation_changes', ['publish_date'])

    op.create_table(
        'compliance_alerts',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('watchlist_id', sa.String(36), nullable=False),
        sa.Column('regulation_change_id', sa.String(36), nullable=False),
        sa.Column('risk_level', sa.String(16), nullable=False, server_default='中'),
        sa.Column('matched_keyword', sa.String(128), nullable=False, server_default=''),
        sa.Column('analysis', sa.Text(), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='open'),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['watchlist_id'], ['compliance_watchlists.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['regulation_change_id'], ['regulation_changes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('watchlist_id', 'regulation_change_id',
                            name='uq_compliance_alerts_pair'),
    )
    op.create_index('ix_compliance_alerts_user_id', 'compliance_alerts', ['user_id'])
    op.create_index('ix_compliance_alerts_status', 'compliance_alerts', ['status'])


def downgrade() -> None:
    op.drop_table('compliance_alerts')
    op.drop_table('regulation_changes')
    op.drop_table('compliance_watchlists')
