# -*- coding: utf-8 -*-
"""Unit tests for law_retrieval_agent article-reference helpers."""
import sys

sys.path.insert(0, r"d:\Legal Intelligent Assistance System\backend")

from app.agents.law_retrieval_agent import (
    _arabic_to_chinese_num,
    _canonical_article_number,
    _extract_article_refs,
)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"[PASS] {name}")
    else:
        failed += 1
        print(f"[FAIL] {name}: got={got!r} want={want!r}")


# --- Arabic -> Chinese numerals ---
for n, want in [
    (1, "一"), (7, "七"), (9, "九"),
    (10, "十"), (12, "十二"), (19, "十九"),
    (20, "二十"), (42, "四十二"),
    (100, "一百"), (107, "一百零七"), (110, "一百一十"),
    (340, "三百四十"), (342, "三百四十二"),
    (666, "六百六十六"), (966, "九百六十六"),
    (1005, "一千零五"), (1079, "一千零七十九"), (1260, "一千二百六十"),
    (9999, "九千九百九十九"),
]:
    check(f"num {n}", _arabic_to_chinese_num(n), want)
check("num 0 empty", _arabic_to_chinese_num(0), "")
check("num 10000 empty", _arabic_to_chinese_num(10000), "")

# --- canonical article numbers ---
check("arabic 342", _canonical_article_number("342", None), "第三百四十二条")
check("chinese passthrough", _canonical_article_number("三百四十", None), "第三百四十条")
check("with zhi suffix", _canonical_article_number("133", "之一"), "第一百三十三条之一")
check("zero char", _canonical_article_number("〇", None), "")

# --- reference extraction ---
refs = _extract_article_refs("民法典第三百四十条 土地经营权")
check("refs count", len(refs), 1)
check("ref chunk", refs[0]["law_chunk"], "民法典")
check("ref number", refs[0]["article_number"], "第三百四十条")

refs = _extract_article_refs("民法典第342条 招标拍卖公开协商承包农村土地")
check("arabic ref chunk", refs[0]["law_chunk"], "民法典")
check("arabic ref number", refs[0]["article_number"], "第三百四十二条")

refs = _extract_article_refs("请问 民法典第1079条 诉讼离婚")
check("prefixed ref", (refs[0]["law_chunk"], refs[0]["article_number"]), ("民法典", "第一千零七十九条"))

refs = _extract_article_refs("《劳动合同法》第十条")
check("book-title ref", (refs[0]["law_chunk"], refs[0]["article_number"]), ("劳动合同法", "第十条"))

refs = _extract_article_refs("民法典第340条和第342条")
check("multi-ref count", len(refs), 2)
check("multi-ref 1", (refs[0]["law_chunk"], refs[0]["article_number"]), ("民法典", "第三百四十条"))
check("multi-ref 2", (refs[1]["law_chunk"], refs[1]["article_number"]), ("民法典", "第三百四十二条"))

refs = _extract_article_refs("租房押金不退怎么办")
check("no refs", len(refs), 0)

refs = _extract_article_refs("刑法第一百三十三条之一 危险驾驶")
check("zhi ref", (refs[0]["law_chunk"], refs[0]["article_number"]), ("刑法", "第一百三十三条之一"))

refs = _extract_article_refs("夫妻离婚财产怎么分")
check("no false positive on 分", len(refs), 0)

refs = _extract_article_refs("民法典第九百六十六条 中介合同")
check("chinese full form", (refs[0]["law_chunk"], refs[0]["article_number"]), ("民法典", "第九百六十六条"))

print(f"\n{passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
