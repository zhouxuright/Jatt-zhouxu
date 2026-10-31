"""RAG 检索质量回归评测。

对 ``scripts/eval/golden_set.json`` 中的标注问题跑完整检索链路
（BM25 + Milvus 向量 + 知识图谱 + RRF + rerank），输出：

- Hit@1 / Hit@3 / Hit@5
- Recall@k（单条 query 可能有多个期望法条）
- MRR@5
- nDCG@5（二值相关性）
- 单次检索延迟 P50 / P95 / max
- 无结果率

用法（在 backend 目录下执行）::

    python scripts/rag_eval.py
    python scripts/rag_eval.py --top-k 10 --json eval_report.json
    python scripts/rag_eval.py --no-rerank          # A/B：关闭 cross-encoder

生产环境容器内执行::

    docker exec -i <backend> python scripts/rag_eval.py

注意：脚本默认直接调用进程内检索管线，需要能连上 PostgreSQL / Milvus。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_GOLDEN = Path(__file__).resolve().parent / "eval" / "golden_set.json"
CUTOFFS = (1, 3, 5)

_CN_NUM = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000,
    "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
}
_ARTICLE_RE = re.compile(r"第([0-9]+|[零〇一二三四五六七八九十百千壹贰叁肆伍陆柒捌玖]+)条")


def _cn_to_int(text: str) -> int | None:
    """把中文数字（一千零四十六）转成整数，失败返回 None。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)

    total = 0
    current = 0
    seen = False
    for ch in text:
        if ch not in _CN_NUM:
            return None
        seen = True
        value = _CN_NUM[ch]
        if value >= 10:
            if current == 0:
                current = 1
            total += current * value
            current = 0
        else:
            current = value
    if not seen:
        return None
    return total + current


def parse_article_number(text: str) -> int | None:
    """从 '第一千零四十六条' / '第1046条' / '1046' 中解析出条文序号。"""
    if not text:
        return None
    match = _ARTICLE_RE.search(text)
    if match:
        return _cn_to_int(match.group(1))
    digits = re.search(r"[0-9]+", text)
    if digits:
        return int(digits.group())
    return _cn_to_int(text.strip())


def normalize_law_name(name: str) -> str:
    """归一化法律名称，便于宽松匹配（去掉国名前缀、书名号、修正后缀）。"""
    if not name:
        return ""
    text = name.replace("《", "").replace("》", "").replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    text = text.replace("中华人民共和国", "")
    text = re.sub(r"(修正案[一二三四五六七八九十]+|修正|修订)$", "", text)
    return text


def is_relevant(result: dict, expected: dict) -> bool:
    """判断一条检索结果是否命中期望法条。"""
    got_law = normalize_law_name(str(result.get("law_name", "")))
    want_law = normalize_law_name(str(expected.get("law_name", "")))
    if want_law and not (want_law in got_law or got_law in want_law):
        return False

    want_article = parse_article_number(str(expected.get("article_number", "")))
    if want_article is None:
        return True
    got_article = parse_article_number(
        str(result.get("article_number", "")) or str(result.get("content", ""))[:20]
    )
    return got_article == want_article


def ndcg_at_k(hits: list[bool], k: int) -> float:
    """二值相关性 nDCG@k。"""
    dcg = sum(1.0 / math.log2(rank + 2) for rank, hit in enumerate(hits[:k]) if hit)
    ideal = sum(1.0 / math.log2(rank + 2) for rank in range(min(k, sum(hits))))
    return dcg / ideal if ideal else 0.0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def evaluate(golden_path: Path, top_k: int, use_reranker: bool, limit: int | None) -> dict:
    from app.rag.advanced_retriever import get_rag_pipeline

    payload = json.loads(golden_path.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    if limit:
        items = items[:limit]
    if not items:
        raise SystemExit(f"评测集为空: {golden_path}")

    pipeline = get_rag_pipeline()
    pipeline.initialize()

    # 预热一次，避免把模型/索引首次加载计入延迟
    pipeline.retrieve(items[0]["query"], top_k=top_k, use_reranker=use_reranker)

    latencies: list[float] = []
    results: list[dict] = []
    hit_counts = {k: 0 for k in CUTOFFS}
    recall_sums = {k: 0.0 for k in CUTOFFS}
    mrr_sum = 0.0
    ndcg_sum = 0.0
    empty = 0

    for item in items:
        started = time.perf_counter()
        docs = pipeline.retrieve(item["query"], top_k=max(top_k, 5), use_reranker=use_reranker)
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)
        if not docs:
            empty += 1

        expected = item.get("relevant", [])
        flags = [any(is_relevant(doc, exp) for exp in expected) for doc in docs]
        first_rank = next((idx + 1 for idx, flag in enumerate(flags) if flag), None)
        if first_rank:
            mrr_sum += 1.0 / first_rank
            ndcg_sum += ndcg_at_k(flags, 5)

        for k in CUTOFFS:
            if any(flags[:k]):
                hit_counts[k] += 1
            if expected:
                recall_sums[k] += sum(1 for i in range(min(k, len(flags))) if flags[i]) / len(expected)

        results.append({
            "id": item.get("id"),
            "query": item["query"],
            "latency_ms": round(latency_ms, 2),
            "first_relevant_rank": first_rank,
            "hit_at_5": any(flags[:5]),
            "top": [
                {
                    "rank": rank + 1,
                    "law_name": doc.get("law_name", ""),
                    "article_number": doc.get("article_number", ""),
                    "source": doc.get("source", ""),
                    "relevant": flags[rank],
                }
                for rank, doc in enumerate(docs[:5])
            ],
        })

    total = len(items)
    return {
        "golden_set": str(golden_path),
        "queries": total,
        "top_k": top_k,
        "reranker": use_reranker,
        "hit_rate": {f"@{k}": round(hit_counts[k] / total, 4) for k in CUTOFFS},
        "recall": {f"@{k}": round(recall_sums[k] / total, 4) for k in CUTOFFS},
        "mrr@5": round(mrr_sum / total, 4),
        "ndcg@5": round(ndcg_sum / total, 4),
        "empty_rate": round(empty / total, 4),
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
            "max": round(max(latencies), 2),
        },
        "items": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 检索质量回归评测")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN), help="评测集 JSON 路径")
    parser.add_argument("--top-k", type=int, default=5, help="最终返回条数（默认 5）")
    parser.add_argument("--no-rerank", action="store_true", help="关闭 cross-encoder，做 A/B 对比")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条，快速自检")
    parser.add_argument("--json", default=None, help="把完整结果写入 JSON 文件")
    parser.add_argument("--verbose", action="store_true", help="逐条打印命中的前 5 条")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    report = evaluate(Path(args.golden), args.top_k, not args.no_rerank, args.limit)

    print(f"\nRAG 评测：{report['queries']} 条 query | top_k={report['top_k']} | reranker={report['reranker']}")
    print("=" * 78)
    print(f"Hit@1    {report['hit_rate']['@1']:.2%}    Hit@3   {report['hit_rate']['@3']:.2%}    Hit@5   {report['hit_rate']['@5']:.2%}")
    print(f"Recall@1 {report['recall']['@1']:.2%}    Recall@3 {report['recall']['@3']:.2%}    Recall@5 {report['recall']['@5']:.2%}")
    print(f"MRR@5    {report['mrr@5']:.4f}        nDCG@5   {report['ndcg@5']:.4f}      无结果率 {report['empty_rate']:.2%}")
    print(f"延迟     P50 {report['latency_ms']['p50']}ms      P95 {report['latency_ms']['p95']}ms      max {report['latency_ms']['max']}ms")
    print("=" * 78)

    if args.verbose:
        misses = [row for row in report["items"] if not row["hit_at_5"]]
        if misses:
            print("\n未命中（Top5 内无期望法条）：")
            for row in misses:
                print(f"  [{row['id']}] {row['query']}  ({row['latency_ms']}ms)")
                for hit in row["top"]:
                    print(f"      #{hit['rank']} {hit['law_name']} {hit['article_number']}")

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n完整结果已写入 {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
