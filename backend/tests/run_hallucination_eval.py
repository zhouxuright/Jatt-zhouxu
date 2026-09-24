"""最小法律幻觉评测器。

用法：
1. 单元自检（提取与数字归一化）：
   python tests/run_hallucination_eval.py

2. 对一段给定回答打分（编程调用）：
   from tests.run_hallucination_eval import load_dataset, score_answer
   result = score_answer(case, answer_text)

3. 对接线上 /chat 接口批量评测（可选，需已登录 token）：
   python tests/run_hallucination_eval.py --live --base http://localhost:8000 --token <JWT>

评分口径：
- expected_precision / expected_recall / f1：回答中引用与「应引用法条」的匹配程度。
- forbidden_hits：命中了「严禁出现」法条的数量（视为编造/答非所问信号）。
- db_verified / db_unverified：在 --live 模式下，法条号存在性校验结果。

说明：法条号比较统一归一化为整数，法律名称做双向子串兼容（如 民法典 vs 中华人民共和国民法典）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
DATASET_PATH = _HERE / "hallucination_eval_dataset.json"

# 允许从任意目录运行本脚本时导入 app 包
_BACKEND_ROOT = _HERE.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.citation_verifier import cn_to_int, extract_citations  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _norm_law(name: str) -> str:
    """去掉《》与「中华人民共和国」等前缀，只保留核心名称用于模糊比较。"""
    n = (name or "").strip().strip("《》()（）")
    for prefix in ("中华人民共和国",):
        if n.startswith(prefix):
            n = n[len(prefix):]
    return n


def _law_compatible(a: str, b: str) -> bool:
    """法律名称双向子串兼容（短名 vs 全称）。"""
    a, b = _norm_law(a), _norm_law(b)
    if not a or not b:
        return False
    return a in b or b in a


def _article_int(raw: Any) -> int | None:
    if isinstance(raw, int):
        return raw
    return cn_to_int(str(raw))


# ---------------------------------------------------------------------------
# 数据集加载与打分
# ---------------------------------------------------------------------------

def load_dataset() -> list[dict[str, Any]]:
    with open(DATASET_PATH, encoding="utf-8") as f:
        return json.load(f)["cases"]


def score_answer(case: dict[str, Any], answer_text: str) -> dict[str, Any]:
    """对一个回答打分，返回精确率/召回率/命中禁用项等指标。"""
    expected = [
        (_norm_law(c["law_name"]), _article_int(c["article_number"]))
        for c in case.get("expected_citations", [])
    ]
    forbidden = [
        (_norm_law(c["law_name"]), _article_int(c["article_number"]))
        for c in case.get("forbidden_citations", [])
    ]

    extracted = extract_citations(answer_text or "")
    extracted_pairs = [
        (_norm_law(c["law_name"]), c["article_number_int"])
        for c in extracted
    ]

    def _hit(pair: tuple[str, int | None], pool: list[tuple[str, int | None]]) -> bool:
        law, num = pair
        for plaw, pnum in pool:
            if num is not None and pnum is not None and num != pnum:
                continue
            if _law_compatible(law, plaw):
                return True
        return False

    matched_expected = [p for p in extracted_pairs if _hit(p, expected)]
    forbidden_hits = [p for p in extracted_pairs if _hit(p, forbidden)]

    precision = len(matched_expected) / len(extracted_pairs) if extracted_pairs else 0.0
    recall = len(matched_expected) / len(expected) if expected else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "case_id": case.get("id"),
        "category": case.get("category"),
        "extracted_citations": [
            {"law_name": c["law_name"], "article_number": c["article_number_raw"]}
            for c in extracted
        ],
        "expected_count": len(expected),
        "matched_expected": len(matched_expected),
        "forbidden_hits": len(forbidden_hits),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "hallucination_suspect": forbidden_hits > 0,
    }


# ---------------------------------------------------------------------------
# 单元自检
# ---------------------------------------------------------------------------

def _self_check() -> int:
    ok = True

    def _expect(name: str, got: Any, want: Any) -> None:
        nonlocal ok
        if got != want:
            ok = False
            print(f"  [FAIL] {name}: got={got!r} want={want!r}")
        else:
            print(f"  [PASS] {name}")

    print("== cn_to_int 自检 ==")
    _expect("五百八十四 -> 584", cn_to_int("五百八十四"), 584)
    _expect("三十八 -> 38", cn_to_int("三十八"), 38)
    _expect("八十五 -> 85", cn_to_int("八十五"), 85)
    _expect("十 -> 10", cn_to_int("十"), 10)
    _expect("一千二百零八 -> 1208", cn_to_int("一千二百零八"), 1208)
    _expect("666 -> 666", cn_to_int("666"), 666)

    print("== extract_citations 自检 ==")
    sample = (
        "根据《中华人民共和国劳动合同法》第三十八条规定，劳动者可以解除劳动合同；"
        "另据《民法典》第584条，违约损失赔偿以可预见损失为限。"
    )
    cites = extract_citations(sample)
    _expect("提取到2条", len(cites), 2)
    if cites:
        _expect("第1条法律名", cites[0]["law_name"], "中华人民共和国劳动合同法")
        _expect("第1条条号int", cites[0]["article_number_int"], 38)
        _expect("第2条条号int", cites[1]["article_number_int"], 584)

    print("== 数据集加载自检 ==")
    cases = load_dataset()
    _expect("用例数量=10", len(cases), 10)

    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 线上 /chat 批量评测（可选）
# ---------------------------------------------------------------------------

def _run_live(base: str, token: str) -> int:
    import asyncio

    import httpx

    async def _eval() -> int:
        cases = load_dataset()
        headers = {"Authorization": f"Bearer {token}"}
        print(f"共 {len(cases)} 条用例，逐条调用 {base}/api/v1/chat ...\n")
        async with httpx.AsyncClient(base_url=base, timeout=60) as client:
            for case in cases:
                try:
                    resp = await client.post(
                        "/api/v1/chat",
                        json={"message": case["query"]},
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        print(f"[{case['id']}] HTTP {resp.status_code}: {resp.text[:120]}")
                        continue
                    answer = resp.json().get("content", "")
                    meta = resp.json().get("metadata", {})
                except Exception as exc:
                    print(f"[{case['id']}] 请求失败: {exc}")
                    continue
                r = score_answer(case, answer)
                veri = meta.get("citation_verification") or {}
                print(
                    f"[{case['id']}] {case['category']}\n"
                    f"  precision={r['precision']} recall={r['recall']} f1={r['f1']} "
                    f"forbidden_hits={r['forbidden_hits']}\n"
                    f"  校验回路: verified={veri.get('verified_count', '-')} "
                    f"unverified={veri.get('unverified_count', '-')}\n"
                )
        return 0

    return asyncio.run(_eval())


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="最小法律幻觉评测器")
    parser.add_argument("--live", action="store_true", help="对接线上 /chat 接口批量评测")
    parser.add_argument("--base", default="http://localhost:8000", help="后端地址")
    parser.add_argument("--token", default="", help="登录 JWT（Authorization: Bearer）")
    args = parser.parse_args()

    if args.live:
        if not args.token:
            print("--live 模式需要 --token")
            return 2
        return _run_live(args.base, args.token)

    return _self_check()


if __name__ == "__main__":
    raise SystemExit(main())