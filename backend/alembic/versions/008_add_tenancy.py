"""Multi-tenancy isolation (P0-2)

Revision ID: 008
Revises: 007
Create Date: 2026-09-13

为商业交付引入租户维度：

1. 新建 ``tenants`` 表，并插入默认租户（承接存量数据 / 单租户私有化部署）。
2. 为承载客户数据的核心表新增 ``tenant_id``：``users``、``conversations``、
   ``documents``、``contracts``、``audit_logs``。
3. 回填：存量行统一归入默认租户，保证迁移后不出现"孤儿数据"。
4. 建索引：租户维度是高频过滤条件，必须走索引。

应用层强制过滤见 ``app/core/tenancy.py``；RLS 二线防线见
``backend/scripts/enable_rls_hardening.sql``。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '008'
down_revision: Union[str, None] = '007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

#: 需要启用租户隔离的表
TENANT_TABLES = ["users", "conversations", "documents", "contracts", "audit_logs"]


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. tenants 表
    # ------------------------------------------------------------------
    op.create_table(
        'tenants',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('code', sa.String(64), nullable=False),
        sa.Column('status', sa.String(32), nullable=False, server_default='active'),
        sa.Column('plan', sa.String(32), nullable=False, server_default='standard'),
        sa.Column('max_users', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('contact_email', sa.String(256), nullable=True),
        sa.Column('remark', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_tenants_code'),
    )
    op.create_index('ix_tenants_code', 'tenants', ['code'])

    # ------------------------------------------------------------------
    # 2. 默认租户 —— 承接全部存量数据
    # ------------------------------------------------------------------
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO tenants (id, name, code, status, plan, max_users, remark) "
            "VALUES (:id, :name, :code, 'active', 'enterprise', 1000, "
            ":remark) ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": DEFAULT_TENANT_ID,
            "name": "默认租户",
            "code": "default",
            "remark": "系统初始化租户：承接历史存量数据与单租户私有化部署",
        },
    )

    # ------------------------------------------------------------------
    # 3. 各核心表新增 tenant_id + 回填 + 索引
    # ------------------------------------------------------------------
    for table in TENANT_TABLES:
        op.add_column(
            table,
            sa.Column('tenant_id', sa.String(36), nullable=True),
        )
        # 存量数据归入默认租户，避免"孤儿数据"在隔离后不可见。
        bind.execute(
            sa.text(
                f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL"
            ),
            {"tid": DEFAULT_TENANT_ID},
        )
        op.create_index(f'ix_{table}_tenant_id', table, ['tenant_id'])

    # users.tenant_id 加外键（其余表不加，避免删除租户时级联受限）
    op.create_foreign_key(
        'fk_users_tenant_id', 'users', 'tenants',
        ['tenant_id'], ['id'], ondelete='RESTRICT',
    )

    print(f"[008] 已为 {len(TENANT_TABLES)} 张表启用租户隔离，"
          f"存量数据归入默认租户 {DEFAULT_TENANT_ID}")


def downgrade() -> None:
    op.drop_constraint('fk_users_tenant_id', 'users', type_='foreignkey')
    for table in TENANT_TABLES:
        op.drop_index(f'ix_{table}_tenant_id', table_name=table)
        op.drop_column(table, 'tenant_id')
    op.drop_index('ix_tenants_code', table_name='tenants')
    op.drop_table('tenants')
