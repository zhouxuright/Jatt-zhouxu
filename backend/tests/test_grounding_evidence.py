"""P0 回归测试：主张级 grounding、来源分级、评测门禁基线对比。

这些测试不依赖 PostgreSQL / Milvus / LLM，可在 CI 中直接跑，
作为 P0-1 / P0-2 / P0-3 的 merge 门禁。
"""

from __future__ import annotations

import json

import pytest

from app.services.citation_verifier import (
    EVIDENCE_LEVELS,
    describe_evidence,
    evidence_rank,
)
from app.services.grounding import (
    _bigrams,
    _classify,
    _support_score,
    build_grounding_note,
    split_claims,
)
from scripts.rag_eval import compare_with_baseline, metrics_only


# ---------------------------------------------------------------------------
# P0-1 主张级 grounding
# ---------------------------------------------------------------------------

def test_split_claims_keeps_assertion_sentences():
    text = (
        "第一句话只是背景介绍，没有结论。"
        "用人单位解除劳动合同应当支付经济补偿。"
        "《中华人民共和国劳动合同法》第八十七条规定了赔偿标准。"
    )
    claims = split_claims(text)
    assert any("经济补偿" in c for c in claims)
    assert any("第八十七条" in c for c in claims)
    # 纯背景句不含断言词，也不含书名号，应被过滤
    assert all("背景介绍" not in c for c in claims)


def test_split_claims_respects_max_limit():
    from app.core.config import settings

    long_text = "。".join(["该行为应当承担赔偿责任" for _ in range(50)]) + "。"
    claims = split_claims(long_text)
    assert len(claims) <= max(1, int(settings.GROUNDING_MAX_CLAIMS))


def test_support_score_prefers_overlap():
    claim = "用人单位解除劳动合同应当支付经济补偿"
    strong = "用人单位违反本法规定解除劳动合同的，应当依照本法支付经济补偿。"
    weak = "本法所称用人单位，是指中华人民共和国境内的企业、个体经济组织等。"
    assert _support_score(claim, strong) > _support_score(claim, weak)
    assert _support_score(claim, "") == 0.0


def test_bigrams_strip_punctuation_and_digits():
    assert _bigrams("甲，乙。1") == {"甲乙"}
    assert _bigrams("") == set()


def test_classify_thresholds():
    from app.core.config import settings

    assert _classify(float(settings.GROUNDING_SUPPORT_THRESHOLD)) == "supported"
    assert _classify(float(settings.GROUNDING_WEAK_THRESHOLD)) == "weak"
    assert _classify(0.0) == "unsupported"


def test_build_grounding_note_only_for_unsupported():
    ok_result = {"level": "ok", "claims": [{"text": "x", "status": "supported"}]}
    assert build_grounding_note(ok_result) == ""

    flagged = {
        "level": "weak",
        "grounding_score": 0.0,
        "checked": 2,
        "claims": [
            {"text": "该合同当然无效", "status": "unsupported"},
            {"text": "另一条有支撑", "status": "supported"},
        ],
    }
    note = build_grounding_note(flagged)
    assert "该合同当然无效" in note
    assert "证据支撑" in note


# ---------------------------------------------------------------------------
# P0-2 来源分级标签
# ---------------------------------------------------------------------------

def test_evidence_levels_cover_four_tiers():
    assert set(EVIDENCE_LEVELS) == {"statute", "judgment", "secondary", "none"}
    assert EVIDENCE_LEVELS["statute"] == "法条原文"


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("statute", ("statute", "法条原文")),
        ("judgment", ("judgment", "裁判文书")),
        ("secondary", ("secondary", "二手解读")),
        ("none", ("none", "无来源")),
        ("bogus", ("none", "无来源")),
    ],
)
def test_describe_evidence_normalizes(level, expected):
    assert describe_evidence(level) == expected


def test_evidence_rank_orders_by_authority():
    assert evidence_rank("statute") > evidence_rank("judgment") > evidence_rank("secondary")
    assert evidence_rank("none") == 0
    assert evidence_rank("bogus") == 0


# ---------------------------------------------------------------------------
# P0-3 评测门禁：基线对比
# ---------------------------------------------------------------------------

def _report(**overrides) -> dict:
    base = {
        "hit_rate": {"@1": 0.8, "@3": 0.9, "@5": 0.95},
        "recall": {"@1": 0.7, "@3": 0.85, "@5": 0.9},
        "mrr@5": 0.82,
        "ndcg@5": 0.8,
        "empty_rate": 0.02,
    }
    base.update(overrides)
    return base


def test_metrics_only_extracts_gated_metrics():
    values = metrics_only(_report())
    assert set(values) == {
        "hit_rate.@1", "hit_rate.@3", "hit_rate.@5",
        "recall.@1", "recall.@3", "recall.@5",
        "mrr@5", "ndcg@5", "empty_rate",
    }
    assert values["mrr@5"] == 0.82


def test_baseline_equal_has_no_regression():
    report = _report()
    baseline = json.loads(json.dumps(metrics_only(report)))
    assert compare_with_baseline(report, baseline, tolerance=0.02) == []


def test_baseline_detects_hit_rate_drop():
    baseline = json.loads(json.dumps(metrics_only(_report())))
    current = _report(hit_rate={"@1": 0.7, "@3": 0.9, "@5": 0.95})
    regressions = compare_with_baseline(current, baseline, tolerance=0.02)
    assert [r["metric"] for r in regressions] == ["hit_rate.@1"]
    assert regressions[0]["delta"] == pytest.approx(-0.1, abs=1e-6)


def test_baseline_treats_empty_rate_as_lower_is_better():
    baseline = json.loads(json.dumps(metrics_only(_report())))
    current = _report(empty_rate=0.2)
    regressions = compare_with_baseline(current, baseline, tolerance=0.02)
    assert [r["metric"] for r in regressions] == ["empty_rate"]


def test_baseline_within_tolerance_passes():
    baseline = json.loads(json.dumps(metrics_only(_report())))
    current = _report(hit_rate={"@1": 0.79, "@3": 0.89, "@5": 0.94})
    assert compare_with_baseline(current, baseline, tolerance=0.02) == []
