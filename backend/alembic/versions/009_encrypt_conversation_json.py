"""Encrypt conversation JSON payloads (fact sheet / topic segments)

Revision ID: 009
Revises: 008
Create Date: 2026-09-14

``conversations.fact_sheet`` 与 ``topic_segments`` 承载当事人身份、争议焦点、
引用法条等敏感信息，此前以明文 JSON 落盘，是 P0-1 加密清单的最后一处缺口。

本迁移把两列由 ``JSON`` 改为 ``TEXT``，改由 ORM 的 ``EncryptedJSON`` 类型
透明加解密。存量明文 JSON 经 ``::text`` 转换后仍是合法 JSON 字符串，
应用层 ``decrypt()`` 直通分支可正常读取，因此在下次写入时自然完成加密。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '009'
down_revision: Union[str, None] = '008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = ("fact_sheet", "topic_segments")


def upgrade() -> None:
    for column in COLUMNS:
        op.execute(
            f"ALTER TABLE conversations ALTER COLUMN {column} TYPE TEXT "
            f"USING {column}::text"
        )
    print(f"[009] conversations.{', '.join(COLUMNS)} 已改为加密 TEXT 列")


def downgrade() -> None:
    for column in COLUMNS:
        # 回退时按 JSON 解析；若列中已是密文将失败——需先从备份恢复明文。
        op.execute(
            f"ALTER TABLE conversations ALTER COLUMN {column} TYPE JSON "
            f"USING {column}::json"
        )
