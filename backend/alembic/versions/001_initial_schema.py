"""Initial schema - create all tables

Revision ID: 001
Revises: None
Create Date: 2026-08-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('username', sa.String(64), nullable=False),
        sa.Column('email', sa.String(256), nullable=False),
        sa.Column('hashed_password', sa.String(256), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username'),
        sa.UniqueConstraint('email'),
    )
    op.create_index('ix_users_username', 'users', ['username'])
    op.create_index('ix_users_email', 'users', ['email'])
    op.create_index('ix_users_created_at', 'users', ['created_at'])

    # Create conversations table
    op.create_table(
        'conversations',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('title', sa.String(256), nullable=False),
        sa.Column('agent_type', sa.String(64), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_conversations_user_id', 'conversations', ['user_id'])
    op.create_index('ix_conversations_created_at', 'conversations', ['created_at'])

    # Create messages table
    op.create_table(
        'messages',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('conversation_id', sa.String(36), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tokens_used', sa.Integer(), nullable=True),
        sa.Column('metadata', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'])
    op.create_index('ix_messages_created_at', 'messages', ['created_at'])

    # Create feedbacks table
    op.create_table(
        'feedbacks',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('message_id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('feedback_type', sa.String(32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.CheckConstraint('rating >= 1 AND rating <= 5', name='ck_feedback_rating'),
    )
    op.create_index('ix_feedbacks_message_id', 'feedbacks', ['message_id'])
    op.create_index('ix_feedbacks_user_id', 'feedbacks', ['user_id'])
    op.create_index('ix_feedbacks_created_at', 'feedbacks', ['created_at'])

    # Create documents table
    op.create_table(
        'documents',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('filename', sa.String(512), nullable=False),
        sa.Column('original_filename', sa.String(512), nullable=False),
        sa.Column('file_type', sa.String(64), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('file_path', sa.String(1024), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('upload_time', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_documents_user_id', 'documents', ['user_id'])

    # Create contract_reviews table
    op.create_table(
        'contract_reviews',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('document_id', sa.String(36), nullable=True),
        sa.Column('original_filename', sa.String(512), nullable=False),
        sa.Column('risk_score', sa.Float(), nullable=True),
        sa.Column('risk_items', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('full_analysis', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_contract_reviews_user_id', 'contract_reviews', ['user_id'])
    op.create_index('ix_contract_reviews_document_id', 'contract_reviews', ['document_id'])
    op.create_index('ix_contract_reviews_created_at', 'contract_reviews', ['created_at'])

    # Create laws table
    op.create_table(
        'laws',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(512), nullable=False),
        sa.Column('short_name', sa.String(256), nullable=True),
        sa.Column('law_type', sa.String(64), nullable=False),
        sa.Column('category', sa.String(128), nullable=True),
        sa.Column('effective_date', sa.String(32), nullable=True),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('issuing_authority', sa.String(256), nullable=True),
        sa.Column('abstract', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_laws_name', 'laws', ['name'])
    op.create_index('ix_laws_created_at', 'laws', ['created_at'])

    # Create legal_articles table
    op.create_table(
        'legal_articles',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('law_id', sa.String(36), nullable=False),
        sa.Column('article_number', sa.String(64), nullable=False),
        sa.Column('title', sa.String(256), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('chapter', sa.String(128), nullable=True),
        sa.Column('section', sa.String(128), nullable=True),
        sa.Column('effective_status', sa.String(32), nullable=True),
        sa.Column('tags', sa.String(512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['law_id'], ['laws.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_legal_articles_law_id', 'legal_articles', ['law_id'])
    op.create_index('ix_articles_law_number', 'legal_articles', ['law_id', 'article_number'])
    op.create_index('ix_legal_articles_created_at', 'legal_articles', ['created_at'])

    # Create court_cases table
    op.create_table(
        'court_cases',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('case_number', sa.String(128), nullable=False),
        sa.Column('title', sa.String(512), nullable=False),
        sa.Column('court_name', sa.String(256), nullable=True),
        sa.Column('case_type', sa.String(64), nullable=True),
        sa.Column('cause_of_action', sa.String(256), nullable=True),
        sa.Column('decision_date', sa.String(32), nullable=True),
        sa.Column('parties', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('full_text', sa.Text(), nullable=True),
        sa.Column('key_points', sa.Text(), nullable=True),
        sa.Column('referenced_laws', sa.Text(), nullable=True),
        sa.Column('judgment_result', sa.Text(), nullable=True),
        sa.Column('tags', sa.String(512), nullable=True),
        sa.Column('doc_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('case_number'),
    )
    op.create_index('ix_court_cases_court_name', 'court_cases', ['court_name'])
    op.create_index('ix_court_cases_cause_of_action', 'court_cases', ['cause_of_action'])
    op.create_index('ix_cases_cause_type', 'court_cases', ['cause_of_action'])
    op.create_index('ix_cases_court_date', 'court_cases', ['court_name', 'decision_date'])
    op.create_index('ix_court_cases_created_at', 'court_cases', ['created_at'])

    # Create judicial_interpretations table
    op.create_table(
        'judicial_interpretations',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(512), nullable=False),
        sa.Column('doc_number', sa.String(128), nullable=True),
        sa.Column('issuing_court', sa.String(256), nullable=True),
        sa.Column('effective_date', sa.String(32), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('related_law', sa.String(512), nullable=True),
        sa.Column('tags', sa.String(512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_judicial_interpretations_name', 'judicial_interpretations', ['name'])
    op.create_index('ix_judicial_interpretations_created_at', 'judicial_interpretations', ['created_at'])

    # Create legal_concepts table
    op.create_table(
        'legal_concepts',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(256), nullable=False),
        sa.Column('definition', sa.Text(), nullable=False),
        sa.Column('category', sa.String(128), nullable=True),
        sa.Column('related_articles', sa.Text(), nullable=True),
        sa.Column('related_concepts', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index('ix_legal_concepts_name', 'legal_concepts', ['name'])
    op.create_index('ix_legal_concepts_created_at', 'legal_concepts', ['created_at'])

    # Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(64), nullable=True),
        sa.Column('action', sa.String(64), nullable=False),
        sa.Column('resource_type', sa.String(64), nullable=True),
        sa.Column('resource_id', sa.String(64), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(512), nullable=True),
        sa.Column('request_method', sa.String(10), nullable=True),
        sa.Column('request_path', sa.String(512), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('request_summary', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'])
    op.create_index('ix_audit_logs_action', 'audit_logs', ['action'])
    op.create_index('ix_audit_logs_created', 'audit_logs', ['created_at'])
    op.create_index('ix_audit_logs_action_resource', 'audit_logs', ['action', 'resource_type'])
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'])


def downgrade() -> None:
    op.drop_table('audit_logs')
    op.drop_table('legal_concepts')
    op.drop_table('judicial_interpretations')
    op.drop_table('court_cases')
    op.drop_table('legal_articles')
    op.drop_table('laws')
    op.drop_table('contract_reviews')
    op.drop_table('documents')
    op.drop_table('feedbacks')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('users')
