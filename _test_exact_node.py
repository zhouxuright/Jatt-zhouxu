# -*- coding: utf-8 -*-
"""Isolated test of the exact_search node against live PostgreSQL."""
import asyncio
import sys

sys.path.insert(0, "/app")


async def main():
    from app.agents.law_retrieval_agent import LawRetrievalState, create_law_retrieval_agent

    agent = create_law_retrieval_agent()
    queries = [
        "民法典第三百四十条 土地经营权",
        "民法典第342条 招标拍卖公开协商承包农村土地",
        "民法典第九百六十六条 中介合同",
        "民法典第1079条 诉讼离婚",
        "《劳动合同法》第十条",
        "刑法第一百三十三条之一 危险驾驶",
        "租房押金不退怎么办",
    ]
    for q in queries:
        state = LawRetrievalState(query=q)
        out = await agent._exact_search_node(state)
        results = out.get("exact_results", [])
        print(f"\n查询: {q}")
        if not results:
            print("  (no exact match)")
        for r in results:
            print(f"  [{r['source']}] {r['law_name']} {r['article_number']} score={r['relevance_score']}")
            print(f"    {r['article_content'][:50]}")


asyncio.run(main())
