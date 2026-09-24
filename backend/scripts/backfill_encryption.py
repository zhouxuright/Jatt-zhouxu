"""存量敏感数据加密回填（P0-1 第 2 步）。

``EncryptedText`` 只在 ORM **写入**时加密，因此上线代码后，历史数据仍是
明文。本脚本把存量明文逐列加密，实现"零停机灰度迁移"的第二步。

特性
----
* **幂等**：已加密（``enc::v1::`` 前缀）的值跳过，可反复安全执行。
* **分批**：按主键分页，避免一次性把大表读进内存。
* **可审计**：输出每张表加密行数；``--dry-run`` 只统计不写入。
* **失败隔离**：单行失败不影响整体，记录后继续。

用法（容器内）::

    docker compose exec backend python scripts/backfill_encryption.py --dry-run
    docker compose exec backend python scripts/backfill_encryption.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.crypto import encrypt, is_encrypted  # noqa: E402
from app.core.database import async_session_factory  # noqa: E402
from app.models.contract import Contract, ContractKeyDate, ContractVersion  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.document import ContractReview, Document  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.models.user import User  # noqa: E402

#: (模型, [需要加密的列名]) —— 与模型上的 EncryptedText/EncryptedString 保持一致
TARGETS: list[tuple[type, list[str]]] = [
    (User, ["email"]),
    (Message, ["content"]),
    (Conversation, ["summary"]),
    (Document, ["summary", "error_message"]),
    (ContractReview, ["risk_items", "summary", "full_analysis"]),
    (Contract, ["content"]),
    (ContractVersion, ["content", "change_summary"]),
    (ContractKeyDate, ["description"]),
]

#: (表名, 列名) —— ``EncryptedJSON`` 列。
#: 这些列经 ORM 读取后已是 dict/list，无法用 ``is_encrypted()`` 判断，
#: 因此改走原生 SQL 读取原始 TEXT，避免误判与二次加密。
JSON_TARGETS: list[tuple[str, str]] = [
    ("conversations", "fact_sheet"),
    ("conversations", "topic_segments"),
]

BATCH = 500


async def _process_json_column(
    table: str, column: str, dry_run: bool,
) -> tuple[int, int, int]:
    """加密 EncryptedJSON 列的存量明文（返回 扫描/待加密/已写入）。"""
    from sqlalchemy import text as sql_text

    scanned = pending = written = 0
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                sql_text(f"SELECT id, {column} AS raw FROM {table} WHERE {column} IS NOT NULL")
            )
        ).all()
        for row_id, raw in rows:
            scanned += 1
            if raw in (None, "") or not isinstance(raw, str):
                continue
            if is_encrypted(raw):
                continue
            # 只有合法 JSON 才加密——否则 EncryptedJSON 读取时会反序列化失败
            # 并返回 None，等于静默丢数据。宁可跳过并告警。
            try:
                json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                print(f"      [跳过] {table}.{column} id={row_id} 不是合法 JSON，保持原样")
                continue
            pending += 1
            if not dry_run:
                await db.execute(
                    sql_text(f"UPDATE {table} SET {column} = :v WHERE id = :i"),
                    {"v": encrypt(raw), "i": row_id},
                )
                written += 1
        if dry_run:
            await db.rollback()
        else:
            await db.commit()

    return scanned, pending, written


async def _process(model: type, columns: list[str], dry_run: bool) -> tuple[int, int, int]:
    """返回 (扫描行数, 需加密的值数, 实际写入的行数)。"""
    scanned = pending = written = 0
    async with async_session_factory() as db:
        last_id: str | None = None
        while True:
            stmt = select(model).order_by(model.id).limit(BATCH)
            # 游标分页：避免 OFFSET 在大表上退化
            if last_id is not None:
                stmt = stmt.where(model.id > last_id)
            rows = (await db.execute(stmt)).scalars().all()
            if not rows:
                break
            last_id = rows[-1].id

            for row in rows:
                scanned += 1
                touched = False
                for col in columns:
                    value = getattr(row, col, None)
                    if not value or not isinstance(value, str):
                        continue
                    if is_encrypted(value):
                        continue
                    setattr(row, col, encrypt(value))
                    pending += 1
                    touched = True
                if touched and not dry_run:
                    written += 1

            if dry_run:
                await db.rollback()
            else:
                await db.commit()

    return scanned, pending, written


async def main(dry_run: bool) -> None:
    mode = "DRY-RUN（不写入）" if dry_run else "写入"
    print(f"=== 存量数据加密回填 [{mode}] ===")
    total_pending = 0
    total_written = 0
    for model, columns in TARGETS:
        table = model.__tablename__
        try:
            scanned, pending, written = await _process(model, columns, dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"  {table:<20} 失败: {exc}")
            continue
        total_pending += pending
        total_written += written
        print(
            f"  {table:<20} 扫描 {scanned:>6} 行 | 待加密值 {pending:>6} | "
            f"本次写入 {written:>6} 行"
        )

    # EncryptedJSON 列（原生 SQL 通道）
    for table, column in JSON_TARGETS:
        label = f"{table}.{column}"
        try:
            scanned, pending, written = await _process_json_column(table, column, dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"  {label:<20} 失败: {exc}")
            continue
        total_pending += pending
        total_written += written
        print(
            f"  {label:<20} 扫描 {scanned:>6} 行 | 待加密值 {pending:>6} | "
            f"本次写入 {written:>6} 行"
        )

    if dry_run:
        print(f"=== 预演完成：共 {total_pending} 个明文值待加密（未写入） ===")
    else:
        print(f"=== 完成：共加密 {total_written} 行 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="存量敏感数据加密回填")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
