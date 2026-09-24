"""
导入 ClaimGen-CN 非刑事(民事)案例数据到 court_cases 表。

数据源: Josieeee/ClaimGen-CN — 207,748 条真实中国民事案例，覆盖 100 个案由
        (民间借贷 / 买卖合同 / 婚姻家事 / 劳动 / 教育 等)
字段: id, cause(案由), plaintiffFactSegment(事实陈述), claims(诉讼请求)

用法 (host venv_gpu):
    python scripts/import_civil_cases.py --file ClaimGen-CN-test.json          # 测试集 1000 条
    python scripts/import_civil_cases.py --file ClaimGen-CN.json              # 全量 207,748 条
    python scripts/import_civil_cases.py --file ClaimGen-CN.json --stats-only # 仅看案由分布
"""
import argparse
import asyncio
import json
import os
import sys
import uuid
from collections import Counter
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO = "Josieeee/ClaimGen-CN"
DSN = "postgresql://postgres:postgres@localhost:5433/legal_assistant"

CASETYPE_KEYWORDS = {
    "劳动争议": ["劳动", "劳务", "工伤", "社保", "养老", "失业", "公积金", "竞业", "报酬", "工资"],
    "婚姻家庭": ["离婚", "婚姻", "抚养", "赡养", "继承", "探望", "探视", "婚约", "同居",
                "家事", "亲子", "彩礼", "析产", "扶养", "监护"],
    "知识产权": ["知识产权", "著作权", "商标", "专利", "不正当竞争", "商业秘密", "域名", "技术合同"],
}


def map_case_type(cause: str) -> str:
    c = cause or ""
    for ctype, keys in CASETYPE_KEYWORDS.items():
        if any(k in c for k in keys):
            return ctype
    return "民事"


def norm_claims(claims) -> str:
    if isinstance(claims, list):
        return "\n".join(str(x) for x in claims)
    return str(claims or "")


async def run(path: str, limit: int, stats_only: bool) -> None:
    import asyncpg

    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    print(f"[loaded] {len(records)} records from {path}")

    causes = Counter(r.get("cause", "(none)") for r in records)
    print("\n=== 案由(cause)分布 top 60 ===")
    for k, v in causes.most_common(60):
        mapped = map_case_type(k)
        print(f"  [{mapped}] {k}: {v}")

    if stats_only:
        return

    if limit and limit > 0:
        records = records[:limit]
        print(f"[info] 仅导入前 {limit} 条")

    conn = await asyncpg.connect(DSN)
    rows = []
    for r in records:
        cause = (r.get("cause") or "").strip()
        fact = (r.get("plaintiffFactSegment") or "").strip()
        if not fact:
            continue
        ctype = map_case_type(cause)
        claims = norm_claims(r.get("claims"))
        case_no = "CLG-" + uuid.uuid5(uuid.NAMESPACE_DNS, "claimgen_" + fact).hex[:16]
        title = f"{ctype}-{cause}" if cause else f"{ctype}案例"
        rows.append((
            str(uuid.uuid4()),      # id
            case_no,                # case_number
            title[:512],            # title
            "",                     # court_name
            ctype,                  # case_type
            cause[:256],            # cause_of_action
            "",                     # decision_date
            "",                     # parties
            fact[:500],             # summary
            fact[:60000],           # full_text
            "",                     # key_points
            "",                     # referenced_laws
            claims[:8000],          # judgment_result
            "huggingface,claimgen-cn,civil",  # tags
            1,                      # doc_count
        ))

    stmt = """
        INSERT INTO court_cases
        (id, case_number, title, court_name, case_type, cause_of_action, decision_date,
         parties, summary, full_text, key_points, referenced_laws, judgment_result,
         tags, doc_count)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        ON CONFLICT (case_number) DO NOTHING
    """
    BATCH = 500
    for i in range(0, len(rows), BATCH):
        await conn.executemany(stmt, rows[i:i + BATCH])
        if (i // BATCH) % 20 == 0:
            print(f"  inserted {i}/{len(rows)} ...")

    total = await conn.fetchval("SELECT count(*) FROM court_cases")
    print(f"\n[info] court_cases 总数 = {total}")

    dist = await conn.fetch(
        "SELECT case_type, count(*) FROM court_cases GROUP BY case_type ORDER BY count(*) DESC"
    )
    print("=== court_cases case_type 分布 ===")
    for row in dist:
        print(f"  {row['case_type']}: {row['count']}")

    civil_dist = await conn.fetch(
        "SELECT cause_of_action, count(*) FROM court_cases "
        "WHERE tags LIKE '%claimgen%' GROUP BY cause_of_action ORDER BY count(*) DESC LIMIT 30"
    )
    print("=== 新导入民事案例 top 案由 ===")
    for row in civil_dist:
        print(f"  {row['cause_of_action']}: {row['count']}")

    await conn.close()


def main():
    parser = argparse.ArgumentParser(description="导入 ClaimGen-CN 民事案例")
    parser.add_argument("--file", default="ClaimGen-CN-test.json")
    parser.add_argument("--limit", type=int, default=0, help="0 = 全部")
    parser.add_argument("--stats-only", action="store_true")
    args = parser.parse_args()

    # HF_TOKEN 只从环境变量读取。此前这里硬编码了一枚真实 token —— 那会让凭证
    # 随源码进入版本库（本项目已推送到公开仓库），因此改为强制由外部注入：
    #   HF_TOKEN=hf_xxx python scripts/import_civil_cases.py --file ClaimGen-CN.json
    if not os.environ.get("HF_TOKEN"):
        print("错误：未设置 HF_TOKEN 环境变量。", file=sys.stderr)
        print("      ClaimGen-CN 是受限数据集，需要 token 才能下载。用法：", file=sys.stderr)
        print("      HF_TOKEN=hf_xxx python scripts/import_civil_cases.py --file ClaimGen-CN.json",
              file=sys.stderr)
        sys.exit(1)

    path = hf_hub_download(repo_id=REPO, filename=args.file, repo_type="dataset")
    print(f"[downloaded] {path}")
    asyncio.run(run(path, args.limit, args.stats_only))


if __name__ == "__main__":
    main()