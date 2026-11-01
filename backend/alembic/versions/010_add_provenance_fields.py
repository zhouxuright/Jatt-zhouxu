"""Add provenance fields: source and generation_method to legal tables

Revision ID: 010
Revises: 009
Create Date: 2026-10-08

Adds provenance / source tracking columns so the RAG pipeline can
distinguish real legal data from synthetic / generated records at
retrieval time and filter the latter out of search results.

Columns added:
  - court_cases.source (String(128), indexed)
  - court_cases.generation_method (String(32), default 'real')
  - legal_qa_pairs.generation_method (String(32), default 'real')
  - legal_knowledge_entries.generation_method (String(32), default 'real')
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '010'
down_revision: Union[str, None] = '009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # court_cases: add source + generation_method
    op.add_column('court_cases', sa.Column('source', sa.String(128), nullable=True))
    op.add_column('court_cases', sa.Column('generation_method', sa.String(32),
                                           nullable=True, server_default='real'))
    op.create_index('ix_court_cases_source', 'court_cases', ['source'])

    # legal_qa_pairs: add generation_method (source already exists)
    op.add_column('legal_qa_pairs', sa.Column('generation_method', sa.String(32),
                                              nullable=True, server_default='real'))

    # legal_knowledge_entries: add generation_method (source already exists)
    op.add_column('legal_knowledge_entries', sa.Column('generation_method', sa.String(32),
                                                       nullable=True, server_default='real'))


def downgrade() -> None:
    op.drop_column('legal_knowledge_entries', 'generation_method')
    op.drop_column('legal_qa_pairs', 'generation_method')
    op.drop_index('ix_court_cases_source', table_name='court_cases')
    op.drop_column('court_cases', 'generation_method')
    op.drop_column('court_cases', 'source')
