"""校验 Milvus 检索路径的**去重兜底**是否生效（P2-6 配套回归）。

背景
----
向量库曾因历史位置型主键与 gap-sync 各写一份而产生大量冗余实体
（详见 ``scripts/dedup_milvus_articles.py``）。物理清理后仍可能在重启、
重跑导入时再次产生冗余，因此检索层加了超采样 + 去重兜底。

本脚本直接调用 ``MilvusRAGService.search()``，断言：
1. 返回结果内部**不存在"同一法条 + 同一正文"的重复条目**；
2. 结果条数等于请求的 top_k（去重后没有把名额浪费掉，说明超采样生效）；
3. 同一 (法名, 条号) 的**不同版本**允许同时出现（不能被误折叠）。

用法::
    docker exec legalintelligentassistancesystem-backend-5 \
        python scripts/verify_vector_search_dedup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.milvus_service import get_milvus_rag_service  # noqa: E402
from app.rag.milvus_schema import business_key  # noqa: E402

TOP_K = 10

QUERIES = [
    "用人单位违法解除劳动合同应当支付多少赔偿金",
    "高空抛物造成他人损害由谁承担责任",
    "夫妻一方要求离婚应当先调解吗",
    "国家工作人员非法收受他人财物为他人谋取利益",
    "政府信息公开申请应当在多少日内答复",
    "机动车交通事故责任如何划分",
]


def _sig(text: str) -> str:
    return "".join((text or "").split())[:2000]


def main() -> int:
    svc = get_milvus_rag_service()
    # 冷启动时 _collection_ready 未置位，先显式初始化（幂等；集合已存在则直接返回 True）
    if not svc.create_collection():
        print("[FAIL] 无法连接/创建 legal_articles 集合")
        return 1
    stats = svc.get_stats()
    print(f"集合状态: {stats}")
    if stats.get("status") != "ready":
        print("[FAIL] 集合不可用")
        return 1

    failures = 0
    for q in QUERIES:
        hits = svc.search(q, top_k=TOP_K)
        keys = [(business_key(h["law_name"], h["article_number"]), _sig(h["content"])) for h in hits]
        dup = len(keys) - len(set(keys))
        # 同一 (法名, 条号) 的不同版本数
        by_bk: dict[str, int] = {}
        for bk, _ in keys:
            by_bk[bk] = by_bk.get(bk, 0) + 1
        multi_ver = {k: v for k, v in by_bk.items() if v > 1}

        ok = dup == 0 and len(hits) == TOP_K
        failures += 0 if ok else 1
        flag = "[PASS]" if ok else "[FAIL]"
        print(f"\n{flag} 「{q}」 → {len(hits)} 条（要求 {TOP_K}），重复条目 {dup}")
        for h in hits[:3]:
            print(f"        {h['score']:.4f}  《{h['law_name'][:22]}》{h['article_number']}")
        if multi_ver:
            print(f"        （同一条号多版本共存 {len(multi_ver)} 处，属预期：法律修订版本）")

    print(f"\n结果：{'全部通过' if failures == 0 else f'{failures} 项失败'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
