"""Tests for the judicial-interpretation population script's pure helpers.

The script itself needs PostgreSQL, so the database work is exercised by running
it against the live stack. What is covered here is the text assembly, which is
where the bugs were: a bare digit regex read `324314` out of an internal-ID
article number, and Chinese numerals sorted lexicographically put 第十一条
before 第十条.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / \
    "populate_judicial_interpretations.py"


def _load_module():
    """Import the script without executing its `main()`."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    # Drop the `if __name__ == "__main__"` tail; importing normally would be
    # fine but the module also configures logging at import time.
    namespace: dict = {"__file__": str(SCRIPT_PATH), "__name__": "populate_ji_test"}
    spec = importlib.util.spec_from_loader("populate_ji_test", loader=None)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    exec(compile(source, str(SCRIPT_PATH), "exec"), namespace)  # noqa: S102
    for key, value in namespace.items():
        setattr(module, key, value)
    return module


ji = _load_module()


class TestChineseNumeralParsing:
    """`_cn_to_int` must read statute numbering, and reject non-numbers."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("第一条", 1),
            ("第七条", 7),
            ("第十条", 10),
            ("第十一条", 11),
            ("第十二条", 12),
            ("第十五条", 15),
            ("第十七条", 17),
            ("第二十条", 20),
            ("第二十九条", 29),
            ("第四十七条", 47),
            ("第一百零三条", 103),
            ("第一百一十条", 110),
            ("第一千零七十九条", 1079),
            ("第一千二百六十条", 1260),
            ("第47条", 47),
            ("47", 47),
        ],
    )
    def test_parses_article_numbers(self, text: str, expected: int):
        assert ji._cn_to_int(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",
            None,
            "附则",
            "__fe324314bad733fa",   # leaked internal id
            "__6b73a24a098d89cc",
            "前言",
        ],
    )
    def test_rejects_non_article_numbers(self, text):
        assert ji._cn_to_int(text) is None

    def test_internal_id_does_not_leak_a_number(self):
        """A bare `\\d+` search would return 324314 for this value.

        Sorting on that would file a document's single article after article
        1260 of a different statute.
        """
        assert ji._cn_to_int("__fe324314bad733fa") is None


class TestArticleSortKey:
    """Articles must be emitted in statute order, not text order."""

    def test_sorts_numerically_not_lexicographically(self):
        articles = [
            {"number": "第十条"},
            {"number": "第十一条"},
            {"number": "第二条"},
            {"number": "第一条"},
        ]
        ordered = [a["number"] for a in sorted(articles, key=ji._article_sort_key)]
        assert ordered == ["第一条", "第二条", "第十条", "第十一条"]

    def test_numbered_articles_precede_unnumbered(self):
        articles = [{"number": "附则"}, {"number": "第一条"}, {"number": ""}]
        ordered = [a["number"] for a in sorted(articles, key=ji._article_sort_key)]
        assert ordered[0] == "第一条"


class TestContentComposition:
    """`_compose_content` assembles the stored body."""

    def _record(self, articles, abstract=None):
        return {
            "law_id": "test-law",
            "name": "测试解释",
            "abstract": abstract,
            "articles": articles,
        }

    def test_articles_are_emitted_in_numeric_order(self):
        record = self._record([
            {"number": "第十条", "content": "第十条正文", "chapter": None, "section": None},
            {"number": "第二条", "content": "第二条正文", "chapter": None, "section": None},
            {"number": "第一条", "content": "第一条正文", "chapter": None, "section": None},
        ])
        content = ji._compose_content(record)
        assert content.index("第一条正文") < content.index("第二条正文") < content.index("第十条正文")

    def test_internal_id_article_number_is_not_printed(self):
        record = self._record([
            {
                "number": "__fe324314bad733fa",
                "content": "最高人民检察院关于印发《规定》的通知",
                "chapter": None,
                "section": None,
            },
        ])
        content = ji._compose_content(record)
        assert "__fe324314bad733fa" not in content
        assert "最高人民检察院关于印发" in content

    def test_abstract_is_placed_first(self):
        record = self._record(
            [{"number": "第一条", "content": "正文", "chapter": None, "section": None}],
            abstract="本解释摘要",
        )
        content = ji._compose_content(record)
        assert content.startswith("本解释摘要")

    def test_chapter_headers_are_not_repeated(self):
        record = self._record([
            {"number": "第一条", "content": "甲", "chapter": "第一章 总则", "section": None},
            {"number": "第二条", "content": "乙", "chapter": "第一章 总则", "section": None},
        ])
        content = ji._compose_content(record)
        assert content.count("第一章 总则") == 1

    def test_empty_articles_yield_empty_content(self):
        assert ji._compose_content(self._record([])) == ""


class TestDedupeByName:
    """Same-named source rows collapse to the one with the most text."""

    def test_prefers_richest_body(self):
        records = [
            {"law_id": "a", "name": "同名解释",
             "articles": [{"content": "短"}]},
            {"law_id": "b", "name": "同名解释",
             "articles": [{"content": "长得多的一段正文"}]},
        ]
        deduped = ji._dedupe_by_name(records)
        assert len(deduped) == 1
        assert deduped[0]["law_id"] == "b"

    def test_keeps_distinct_names(self):
        records = [
            {"law_id": "a", "name": "解释甲", "articles": [{"content": "x"}]},
            {"law_id": "b", "name": "解释乙", "articles": [{"content": "y"}]},
        ]
        assert len(ji._dedupe_by_name(records)) == 2


class TestDocumentNumber:
    """`_document_number` reads 文号 such as 法释〔2020〕26号."""

    def test_reads_number_from_body_not_just_prefix(self):
        """The 文号 sits in the opening 通知, whose position moves with ordering.

        A prefix-only window made the extracted value depend on article order.
        """
        record = {
            "law_id": "dn-1",
            "name": "测试",
            "abstract": "",
            "articles": [
                # Enough filler that the 文号 falls well past any short prefix.
                {"number": f"第{i}条", "content": "正文" * 200,
                 "chapter": None, "section": None}
                for i in range(1, 8)
            ]
            + [{"number": "第八条", "content": "高检发释字〔2021〕2号",
                "chapter": None, "section": None}],
        }
        ji._CONTENT_CACHE.clear()
        assert ji._document_number(record) == "高检发释字〔2021〕2号"

    def test_returns_none_when_absent(self):
        record = {
            "law_id": "dn-2", "name": "无语号", "abstract": "",
            "articles": [{"number": "第一条", "content": "普通正文",
                          "chapter": None, "section": None}],
        }
        ji._CONTENT_CACHE.clear()
        assert ji._document_number(record) is None


class TestRelatedLaws:
    """`_related_laws` extracts 《…》 citations."""

    def test_extracts_and_deduplicates(self):
        articles = [
            {"content": "依据《中华人民共和国劳动合同法》处理"},
            {"content": "并参照《中华人民共和国劳动合同法》及《民法典》"},
        ]
        related = ji._related_laws(articles)
        assert "中华人民共和国劳动合同法" in related
        assert related.count("中华人民共和国劳动合同法") == 1
        assert "民法典" in related

    def test_returns_none_without_citations(self):
        assert ji._related_laws([{"content": "没有任何书名号引用"}]) is None
