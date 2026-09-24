"""Add pg_trgm GIN indexes to court_cases for fast LIKE substring search

Revision ID: 003
Revises: 002
Create Date: 2026-09-01

Case keyword search matches title/tags/cause_of_action via LIKE '%...%',
which previously required a full table scan over 1M+ rows. These trigram
indexes let PostgreSQL answer those predicates with an index scan.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_index(
        'ix_cases_title_trgm', 'court_cases', ['title'],
        postgresql_using='gin',
        postgresql_ops={'title': 'gin_trgm_ops'},
    )
    op.create_index(
        'ix_cases_tags_trgm', 'court_cases', ['tags'],
        postgresql_using='gin',
        postgresql_ops={'tags': 'gin_trgm_ops'},
    )
    op.create_index(
        'ix_cases_cause_trgm', 'court_cases', ['cause_of_action'],
        postgresql_using='gin',
        postgresql_ops={'cause_of_action': 'gin_trgm_ops'},
    )


def downgrade() -> None:
    op.drop_index('ix_cases_cause_trgm', 'court_cases')
    op.drop_index('ix_cases_tags_trgm', 'court_cases')
    op.drop_index('ix_cases_title_trgm', 'court_cases')