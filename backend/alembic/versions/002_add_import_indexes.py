"""Add import-related indexes and constraints

Revision ID: 002
Revises: 001
Create Date: 2026-08-29

Add unique constraints and indexes to support bulk import of legal data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add unique constraint on laws(name, effective_date)
    op.create_unique_constraint(
        'uq_laws_name_effective_date',
        'laws',
        ['name', 'effective_date']
    )

    # Add unique constraint on legal_articles(law_id, article_number)
    op.create_unique_constraint(
        'uq_legal_articles_law_article',
        'legal_articles',
        ['law_id', 'article_number']
    )

    # Add indexes on laws for import performance
    op.create_index('ix_laws_law_type', 'laws', ['law_type'])
    op.create_index('ix_laws_status', 'laws', ['status'])
    op.create_index('ix_laws_effective_date', 'laws', ['effective_date'])

    # Add partial index on legal_articles for active status
    # PostgreSQL supports partial indexes with WHERE clause
    op.create_index(
        'ix_legal_articles_active',
        'legal_articles',
        ['effective_status'],
        postgresql_where=sa.text("effective_status = 'active'")
    )


def downgrade() -> None:
    op.drop_index('ix_legal_articles_active', 'legal_articles')
    op.drop_index('ix_laws_effective_date', 'laws')
    op.drop_index('ix_laws_status', 'laws')
    op.drop_index('ix_laws_law_type', 'laws')
    op.drop_constraint('uq_legal_articles_law_article', 'legal_articles', type_='unique')
    op.drop_constraint('uq_laws_name_effective_date', 'laws', type_='unique')
