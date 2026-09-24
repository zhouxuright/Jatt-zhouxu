"""清理 Milvus ``legal_articles`` 中的**冗余**实体（P2-6 索引卫生）。

背景（为什么不能简单"每键留一条"）
----------------------------------
实测 ``num_entities = 693,536``，按业务键 ``(法名, 条号)`` 分组后只有
``658,209`` 个键 —— 表面上"重复"了 35,327 个实体（29,249 个键）。

但深入比对正文后发现，这 29,249 个键里只有一部分是真冗余：

============  =======  ==========================================
类别           键数     含义
============  =======  ==========================================
完全一致       17,677   忽略空白后正文完全相同（纯格式差异）
互为前缀          300   一份是另一份的截断版（如 8000 字节截断）
实质不同       11,272   正文完全不同——**同一部法律的修订前后版本**
============  =======  ==========================================

第三类是关键陷阱：《中华人民共和国母婴保健法实施办法》第八条，一版写
"劳动保障、计划生育"，另一版写"人力资源社会保障"；《全国人民代表大会
常务委员会议事规则》第一条两个版本更是完全不同。可见 ``(法名, 条号)``
**不是唯一身份**，法律修订会在库里留下多个版本。若按"每键留一条"执行，
会静默删掉 11,272 个法律版本，属于不可接受的数据损失。

因此本脚本按**前缀簇**去重：

1. 同一业务键内，把"忽略空白后互为前缀"的实体聚为一个**文本簇**；
2. 每个文本簇**只保留正文最长**的一份（最长者信息最全），其余删除；
3. **不同文本簇 = 不同版本，全部保留**，不做任何删除。

产生冗余的根因
--------------
两个历史导入器都按**位置**生成主键并各自写入同一批法条：

- ``app/rag/milvus_full_import.py`` / ``mass_import/milvus_importer.py``
  写 ``art_{batch_start+i:08d}``；
- ``scripts/sync_milvus_gap.py`` 用"法名变体"判断"缺失法"，对其实为**已存在**
  的法条又写了一份 ``pg_{uuid}``（其 docstring 自己承认"full pg_* sync 会
  重复 ~640k art_* 向量"，但变体匹配仍漏判，产生了本次这批重复）。

位置型主键在续跑/重跑时会把同一条法条分配到新位置，从而拿到新主键，
单看主键完全看不出重复，必须按业务键分组才能发现。

安全
----
**默认 dry-run**，只统计并写出清单；必须显式传 ``--execute`` 才真正删除。
删除在 Milvus 中是**逻辑删除**（compaction 前底层数据仍在），且执行前会
写出完整待删清单 CSV 备查。

用法::

    # 只统计（默认）
    docker compose --profile tools run --rm backfill python scripts/dedup_milvus_articles.py

    # 写出待删清单 + 保留版本清单
    docker compose --profile tools run --rm backfill \
      python scripts/dedup_milvus_articles.py \
      --report /app/reports/milvus_duplicates.csv \
      --versions /app/reports/milvus_preserved_versions.csv

    # 真正执行删除
    docker compose --profile tools run --rm backfill \
      python scripts/dedup_milvus_articles.py --execute
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.milvus_schema import business_key  # noqa: E402

MILVUS_HOST = "milvus"
MILVUS_PORT = "19530"
COLLECTION = "legal_articles"
FETCH_BATCH = 300

#: ID 形态（用于同长同簇时的保留优先级；位置型主键无意义故排最后）
_ART_UUID = re.compile(r"^art_[0-9a-fA-F-]{36}$")
_PG_UUID = re.compile(r"^pg_[0-9a-fA-F-]{36}$")
_BARE_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")
_ART_POS = re.compile(r"^art_\d+$")


def _strict(value: str) -> str:
    """删除全部空白后的正文（两个历史导入器写入时都做过空白折叠）。"""
    return re.sub(r"\s+", "", value or "")


def _rank(pid: str) -> tuple[int, str]:
    """同簇保留优先级：带 PG uuid 的形态优先于位置型主键（可溯源，且不会随重跑漂移）。"""
    if _ART_UUID.match(pid):
        return (0, pid)
    if _PG_UUID.match(pid):
        return (1, pid)
    if _BARE_UUID.match(pid):
        return (2, pid)
    if _ART_POS.match(pid):
        return (3, pid)
    return (4, pid)


def _scan_ids() -> defaultdict[str, list[str]]:
    """第一遍：按业务键收集实体 id。"""
    from pymilvus import Collection, connections

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    collection = Collection(COLLECTION)
    collection.load()
    groups: defaultdict[str, list[str]] = defaultdict(list)
    iterator = collection.query_iterator(
        batch_size=8000, expr="", output_fields=["id", "law_name", "article_number"]
    )
    total = 0
    while True:
        batch = iterator.next()
        if not batch:
            iterator.close()
            break
        for item in batch:
            groups[
                business_key(item.get("law_name"), item.get("article_number"))
            ].append(item.get("id"))
        total += len(batch)
        print(f"    [1/2] 扫描 id {total:,} …", end="\r", flush=True)
    print()
    return groups


def _fetch_content(ids: list[str]) -> dict[str, str]:
    """第二遍：批量取回这些实体的正文。"""
    from pymilvus import Collection, connections

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    collection = Collection(COLLECTION)
    collection.load()
    content: dict[str, str] = {}
    for start in range(0, len(ids), FETCH_BATCH):
        chunk = ids[start:start + FETCH_BATCH]
        expr = "id in [" + ",".join(f'"{x}"' for x in chunk) + "]"
        for row in collection.query(expr=expr, output_fields=["id", "content"], limit=len(chunk)):
            content[row["id"]] = row.get("content") or ""
        print(f"    [2/2] 取回正文 {min(start + FETCH_BATCH, len(ids)):,}/{len(ids):,}",
              end="\r", flush=True)
    print()
    return content


def _plan(groups: defaultdict[str, list[str]],
          content: dict[str, str]) -> tuple[list[tuple[str, str, str, str]], list[tuple[str, str]]]:
    """返回 (待删列表[(delete_id, key, kept_id, reason)], 保留的版本键[(key, 簇数)])。"""
    to_delete: list[tuple[str, str, str, str]] = []
    multi_version: list[tuple[str, str]] = []

    for key, ids in groups.items():
        if len(ids) < 2:
            continue
        # 按正文长度降序，贪心聚簇：与簇首互为前缀 => 同簇
        clusters: list[list[str]] = []
        for pid in sorted(ids, key=lambda x: -len(_strict(content.get(x, "")))):
            sig = _strict(content.get(pid, ""))
            placed = False
            for cluster in clusters:
                head = _strict(content.get(cluster[0], ""))
                if sig and head and (sig.startswith(head) or head.startswith(sig)):
                    cluster.append(pid)
                    placed = True
                    break
            if not placed:
                clusters.append([pid])

        if len(clusters) > 1:
            multi_version.append((key, str(len(clusters))))

        for cluster in clusters:
            # 保留优先级：**正文长度优先**（越长信息越全，越可能是未截断版本），
            # 长度相同时才比 ID 形态。若反过来先比 ID，会出现"保留了较短的截断版、
            # 删掉了更完整的版本"（实测 304 处），属于反向的信息损失。
            cluster.sort(key=lambda p: (-len(_strict(content.get(p, ""))), _rank(p)))
            keep = cluster[0]
            keep_sig = _strict(content.get(keep, ""))
            for pid in cluster[1:]:
                sig = _strict(content.get(pid, ""))
                if sig == keep_sig:
                    reason = "exact_dup"
                elif keep_sig.startswith(sig):
                    reason = "prefix_of_kept"          # 被保留者是更完整版本
                else:
                    reason = "prefix_of_dropped"       # 罕见：保留者反而是截断版
                to_delete.append((pid, key, keep, reason))
    return to_delete, multi_version


def main(execute: bool, report: str | None, versions: str | None) -> None:
    groups = _scan_ids()
    total_entities = sum(len(v) for v in groups.values())
    dup_ids = sorted({i for v in groups.values() if len(v) > 1 for i in v})
    print(f"实体总数 {total_entities:,}｜业务键 {len(groups):,}｜重复键 {sum(1 for v in groups.values() if len(v) > 1):,}"
          f"｜涉及实体 {len(dup_ids):,}")

    content = _fetch_content(dup_ids)
    to_delete, multi_version = _plan(groups, content)

    by_reason: dict[str, int] = defaultdict(int)
    for _, _, _, reason in to_delete:
        by_reason[reason] += 1

    print("\n=== 去重计划 ===")
    print(f"  待删除冗余实体        : {len(to_delete):,}")
    for reason, cnt in sorted(by_reason.items(), key=lambda x: -x[1]):
        print(f"      · {reason:<20} {cnt:>8,}")
    print(f"  保留的**多版本**业务键 : {len(multi_version):,}（不做任何删除，避免丢失法律修订版本）")
    print(f"  去重后实体数（预估）   : {total_entities - len(to_delete):,}")

    if report:
        with open(report, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["delete_id", "law_name", "article_number", "kept_id", "reason"])
            for pid, key, keep, reason in to_delete:
                name, art = key.split("\u0001")
                writer.writerow([pid, name, art, keep, reason])
        print(f"\n待删清单已写入: {report}")

    if versions:
        with open(versions, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["law_name", "article_number", "version_count"])
            for key, n in multi_version:
                name, art = key.split("\u0001")
                writer.writerow([name, art, n])
        print(f"多版本保留清单已写入: {versions}")

    if not execute:
        print("\n[dry-run] 未做任何删除。确认清单后加 --execute 执行。")
        return

    from pymilvus import Collection, connections

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    collection = Collection(COLLECTION)
    # 注意：本版 pymilvus 的 Collection.delete() 只接受**表达式字符串**，
    # 传主键列表会抛 ParamError("expect non-empty str")。故手工拼 id in [...]。
    # 批大小取 500：过长的表达式会超 Milvus 的 expr 长度限制。
    batch = 500
    delete_ids = [d[0] for d in to_delete]
    for start in range(0, len(delete_ids), batch):
        chunk = delete_ids[start:start + batch]
        expr = "id in [" + ",".join(f'"{x}"' for x in chunk) + "]"
        collection.delete(expr)
        print(f"      已删除 {min(start + batch, len(delete_ids)):,}/{len(delete_ids):,}",
              end="\r", flush=True)
    collection.flush()
    print()
    print("[OK] 删除已提交并 flush。Milvus 为逻辑删除，num_entities 需等 compaction 后下降；"
          "检索会立即排除已删除实体。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清理 Milvus 法条集合中的冗余实体")
    parser.add_argument("--execute", action="store_true", help="真正执行删除（默认仅统计）")
    parser.add_argument("--report", default=None, help="待删清单 CSV")
    parser.add_argument("--versions", default=None, help="被保留的多版本业务键 CSV")
    args = parser.parse_args()
    main(args.execute, args.report, args.versions)
