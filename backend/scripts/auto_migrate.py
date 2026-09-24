#!/usr/bin/env python3
"""
数据库自动迁移脚本 — 检测模型与数据库的列差异并自动修复。

在Docker容器启动时运行，确保数据库表结构与SQLAlchemy模型同步。

使用方法:
    cd backend
    python scripts/auto_migrate.py
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# 列类型映射 (SQLAlchemy -> PostgreSQL)
TYPE_MAP = {
    "VARCHAR": "VARCHAR",
    "String": "VARCHAR",
    "TEXT": "TEXT",
    "Text": "TEXT",
    "INTEGER": "INTEGER",
    "Integer": "INTEGER",
    "BIGINT": "BIGINT",
    "BigInteger": "BIGINT",
    "FLOAT": "FLOAT",
    "Float": "FLOAT",
    "BOOLEAN": "BOOLEAN",
    "Boolean": "BOOLEAN",
    "JSON": "JSONB",
    "JSONB": "JSONB",
    "DateTime": "TIMESTAMP WITH TIME ZONE",
    "TIMESTAMP": "TIMESTAMP WITH TIME ZONE",
    "UUID": "VARCHAR(36)",
}


async def auto_migrate():
    """自动检测并修复数据库结构差异。"""
    from sqlalchemy import text
    from app.core.database import engine

    # Import all models
    from app.models.user import User
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.models.feedback import Feedback
    from app.models.document import Document

    models = [User, Conversation, Message, Feedback, Document]

    async with engine.begin() as conn:
        # Get existing tables
        result = await conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        ))
        db_tables = set(r[0] for r in result.fetchall())

        for model in models:
            table_name = model.__tablename__
            model_cols = {c.name: c for c in model.__table__.columns}

            if table_name not in db_tables:
                # Table doesn't exist - let SQLAlchemy create it
                logger.info("Table %s does not exist, will be created by init_db", table_name)
                continue

            # Get existing columns
            result = await conn.execute(text(
                f"SELECT column_name FROM information_schema.columns WHERE table_name='{table_name}'"
            ))
            db_cols = set(r[0] for r in result.fetchall())

            # Find missing columns
            missing = set(model_cols.keys()) - db_cols

            for col_name in missing:
                col = model_cols[col_name]
                col_type = str(col.type).upper()

                # Map to PostgreSQL type
                pg_type = "TEXT"  # safe default
                for key, val in TYPE_MAP.items():
                    if key.upper() in col_type:
                        pg_type = val
                        break

                # Check for NOT NULL
                nullable = "NULL" if col.nullable else "NOT NULL"

                # Check for default
                default_clause = ""
                if col.default is not None:
                    if hasattr(col.default, 'arg'):
                        default_val = col.default.arg
                        if isinstance(default_val, str):
                            default_clause = f"DEFAULT '{default_val}'"
                        elif callable(default_val):
                            # Skip callable defaults (like uuid4)
                            default_clause = ""
                        else:
                            default_clause = f"DEFAULT {default_val}"

                # Build ALTER TABLE statement
                sql = f'ALTER TABLE {table_name} ADD COLUMN {col_name} {pg_type} {nullable} {default_clause}'.strip()

                try:
                    await conn.execute(text(sql))
                    logger.info("✅ Added column %s.%s (%s)", table_name, col_name, pg_type)
                except Exception as e:
                    logger.warning("⚠️  Failed to add column %s.%s: %s", table_name, col_name, e)

        logger.info("Database migration check complete.")


if __name__ == "__main__":
    asyncio.run(auto_migrate())
