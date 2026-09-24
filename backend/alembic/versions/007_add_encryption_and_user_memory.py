"""Field-level encryption (P0-1) + cross-session long-term memory (P1-5)

Revision ID: 007
Revises: 006
Create Date: 2026-09-13

本迁移做三件事：

1. ``users.email`` 由明文改为加密存储 —— 放宽列宽至 512（Fernet 密文比
   明文长约 100 字符），并新增 ``email_bidx`` 盲索引列承担唯一性与
   "按邮箱登录/查重"的等值检索。
2. 回填存量用户的盲索引，并就地加密存量邮箱（``app.core.crypto`` 提供
   向后兼容解密，故即使回填中断，旧明文仍可读，不会导致登录失败）。
3. 新建 ``user_memory`` 表，承载跨会话长期记忆（事实档案持久化）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '007'
down_revision: Union[str, None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. users.email —— 放宽列宽（必须先于加密写入）
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE users ALTER COLUMN email TYPE VARCHAR(512)")

    # ------------------------------------------------------------------
    # 2. users.email_bidx —— 盲索引
    # ------------------------------------------------------------------
    op.add_column(
        'users',
        sa.Column(
            'email_bidx', sa.String(64), nullable=True,
            comment='Blind index (HMAC-SHA256) of the encrypted email',
        ),
    )
    op.create_index('ix_users_email_bidx', 'users', ['email_bidx'], unique=True)

    # ------------------------------------------------------------------
    # 3. user_memory —— 跨会话长期记忆
    # ------------------------------------------------------------------
    op.create_table(
        'user_memory',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column(
            'profile_json', sa.Text(), nullable=True,
            comment='加密的 LegalFactSheet JSON 快照',
        ),
        sa.Column(
            'profile_text', sa.Text(), nullable=True,
            comment='加密的自然语言用户画像（注入新会话首轮上下文）',
        ),
        sa.Column('entry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_conversation_id', sa.String(36), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_user_memory_user_id'),
    )
    op.create_index('ix_user_memory_user_id', 'user_memory', ['user_id'])

    # ------------------------------------------------------------------
    # 4. 存量数据回填：盲索引 + 邮箱加密
    # ------------------------------------------------------------------
    _backfill_users()


def _backfill_users() -> None:
    """就地回填盲索引并加密存量邮箱。

    使用应用层 ``crypto`` 保证与应用写入路径**完全一致**的算法，
    避免"迁移算出的摘要与线上查不到的摘要"这种隐蔽故障。
    """
    try:
        from app.core.crypto import blind_index, decrypt, encrypt, is_encrypted
    except Exception as exc:  # noqa: BLE001 - 迁移不应因导入失败而中断 DDL
        print(f"[007] 跳过回填：无法导入 app.core.crypto ({exc})")
        return

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, email FROM users WHERE email IS NOT NULL")
    ).fetchall()

    updated = 0
    for user_id, stored in rows:
        if not stored:
            continue
        # 密文 -> 需要先解密才能算盲索引；明文 -> 直接算
        try:
            plain = decrypt(stored) if is_encrypted(stored) else stored
        except Exception:  # noqa: BLE001
            print(f"[007] 用户 {user_id} 邮箱无法解密，已跳过")
            continue
        if plain.startswith("[解密失败]"):
            continue

        bind.execute(
            sa.text(
                "UPDATE users SET email = :email, email_bidx = :bidx WHERE id = :id"
            ),
            {
                "email": encrypt(plain),
                "bidx": blind_index(plain, "user.email"),
                "id": user_id,
            },
        )
        updated += 1

    print(f"[007] 已回填并加密 {updated} 个用户邮箱（共扫描 {len(rows)} 行）")


def downgrade() -> None:
    op.drop_index('ix_user_memory_user_id', table_name='user_memory')
    op.drop_table('user_memory')
    op.drop_index('ix_users_email_bidx', table_name='users')
    op.drop_column('users', 'email_bidx')
    # 注意：email 已加密，无法自动还原为明文；如需降级请从备份恢复。
    # 列宽保持 512 —— 缩小列宽可能截断数据，属破坏性操作，故不执行。
