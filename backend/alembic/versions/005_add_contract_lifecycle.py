"""Add contract lifecycle tables: contracts, contract_versions, contract_key_dates

Revision ID: 005
Revises: 004
Create Date: 2026-09-11

Closes the P1.3 contract lifecycle loop: drafted contracts, their version
history and extracted key dates are persisted, with an explicit archived state.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'contracts',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('title', sa.String(512), nullable=False),
        sa.Column('contract_type', sa.String(64), nullable=False, server_default=''),
        sa.Column('status', sa.String(32), nullable=False, server_default='draft'),
        sa.Column('current_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('latest_review_id', sa.String(36), nullable=True),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['latest_review_id'], ['contract_reviews.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_contracts_user_id', 'contracts', ['user_id'])
    op.create_index('ix_contracts_status', 'contracts', ['status'])

    op.create_table(
        'contract_versions',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('contract_id', sa.String(36), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(32), nullable=False, server_default='manual'),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('change_summary', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_contract_versions_contract_id', 'contract_versions', ['contract_id'])
    op.create_index('ix_contract_versions_number', 'contract_versions',
                    ['contract_id', 'version_number'], unique=True)

    op.create_table(
        'contract_key_dates',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('contract_id', sa.String(36), nullable=False),
        sa.Column('date_type', sa.String(64), nullable=False),
        sa.Column('date_value', sa.String(64), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('reminder_days_before', sa.Integer(), nullable=True),
        sa.Column('notified', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_contract_key_dates_contract_id', 'contract_key_dates', ['contract_id'])


def downgrade() -> None:
    op.drop_table('contract_key_dates')
    op.drop_table('contract_versions')
    op.drop_table('contracts')
