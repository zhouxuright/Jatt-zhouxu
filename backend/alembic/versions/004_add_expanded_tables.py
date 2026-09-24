"""Add expanded legal data tables: QA pairs, knowledge entries, cross references

Revision ID: 004
Revises: 003
Create Date: 2026-09-03

Supports the phase-1 data expansion that imports ~3.5M records from local
JSON/JSONL files into PostgreSQL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- legal_qa_pairs ---
    op.create_table(
        'legal_qa_pairs',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('law_name', sa.String(512), nullable=True),
        sa.Column('article_number', sa.String(64), nullable=True),
        sa.Column('law_type', sa.String(64), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('category', sa.String(128), nullable=True),
        sa.Column('source', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_qa_pairs_law_name', 'legal_qa_pairs', ['law_name'])
    op.create_index('ix_qa_pairs_source', 'legal_qa_pairs', ['source'])
    op.create_index('ix_legal_qa_pairs_created_at', 'legal_qa_pairs', ['created_at'])

    # --- legal_knowledge_entries ---
    op.create_table(
        'legal_knowledge_entries',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('entry_type', sa.String(64), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('law_name', sa.String(512), nullable=True),
        sa.Column('law_type', sa.String(64), nullable=True),
        sa.Column('category', sa.String(128), nullable=True),
        sa.Column('source', sa.String(128), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_knowledge_entries_type', 'legal_knowledge_entries', ['entry_type'])
    op.create_index('ix_knowledge_entries_law_name', 'legal_knowledge_entries', ['law_name'])
    op.create_index('ix_knowledge_entries_source', 'legal_knowledge_entries', ['source'])
    op.create_index('ix_legal_knowledge_entries_created_at', 'legal_knowledge_entries', ['created_at'])

    # --- legal_cross_references ---
    op.create_table(
        'legal_cross_references',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('law_name', sa.String(512), nullable=False),
        sa.Column('referenced_laws', sa.Text(), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('ref_type', sa.String(64), nullable=True),
        sa.Column('category', sa.String(128), nullable=True),
        sa.Column('source', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cross_refs_law_name', 'legal_cross_references', ['law_name'])
    op.create_index('ix_cross_refs_source', 'legal_cross_references', ['source'])
    op.create_index('ix_legal_cross_references_created_at', 'legal_cross_references', ['created_at'])


def downgrade() -> None:
    op.drop_table('legal_cross_references')
    op.drop_table('legal_knowledge_entries')
    op.drop_table('legal_qa_pairs')
