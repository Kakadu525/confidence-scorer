from __future__ import annotations

import pytest

from confidence_scorer.checks.property_tests import FunctionCheckResult, PropertyTestCheckResult
from confidence_scorer.checks.review_panel import DISAGREEMENT_SPREAD, PanelMember, ReviewPanelResult, as_panel
from confidence_scorer.checks.second_reviewer import SecondReviewResult
from confidence_scorer.checks.semantic_diff import SemanticChangeResult, SemanticDiffCheckResult
from confidence_scorer.config import Config
from confidence_scorer.scoring import compute_score


def _member(label, weight, confidence=None, issues=None, error=None, verdict=None, summary=None):
    return PanelMember(
        label=label,
        weight=weight,
        result=SecondReviewResult(
            confidence=confidence, issues=issues or [], error=error, verdict=verdict, summary=summary
        ),
    )


def test_weighted_mean_of_answered_members():
    panel = ReviewPanelResult([_member("opus", 2, 95), _member("qwen", 1, 80)])
    assert panel.sub_score == pytest.approx((2 * 95 + 80) / 3)
    assert panel.answered_share == 1.0


def test_severity_ceiling_applies_before_aggregation():
    qwen = _member("qwen", 1, 90, issues=[{"severity": "medium", "description": "x"}])
    panel = ReviewPanelResult([_member("opus", 2, 95), qwen])
    assert panel.sub_score == pytest.approx((2 * 95 + 75) / 3)


def test_failed_members_are_excluded_and_reduce_share():
    panel = ReviewPanelResult(
        [_member("opus", 2, 90), _member("qwen", 1, 60), _member("deepseek", 1, error="нет DEEPSEEK_API_KEY")]
    )
    assert panel.sub_score == pytest.approx(80.0)
    assert panel.answered_share == pytest.approx(0.75)
    assert panel.coverage_fraction == pytest.approx(0.75)


def test_nobody_answered():
    panel = ReviewPanelResult(
        [_member("opus", 2, error="нет ANTHROPIC_API_KEY"), _member("qwen", 1, error="Ollama недоступна")]
    )
    assert panel.sub_score is None
    assert panel.confidence is None
    assert panel.coverage_fraction == 0.0
    assert panel.error == "opus: нет ANTHROPIC_API_KEY; qwen: Ollama недоступна"


def test_verdict_and_summary_from_heaviest_answered():
    panel = ReviewPanelResult(
        [
            _member("qwen", 1, 70, verdict="так себе", summary="q"),
            _member("opus", 2, 90, verdict="ок", summary="o"),
            _member("big-but-failed", 5, error="429"),
        ]
    )
    assert panel.verdict == "ок"
    assert panel.summary == "o"


def test_equal_weights_first_listed_leads():
    panel = ReviewPanelResult([_member("a", 1, 70, verdict="A"), _member("b", 1, 90, verdict="B")])
    assert panel.verdict == "A"


def test_issues_are_tagged_with_reviewer():
    panel = ReviewPanelResult(
        [
            _member("opus", 2, 60, issues=[{"severity": "high", "description": "граница"}]),
            _member("qwen", 1, 90, issues=[{"severity": "low", "description": "стиль"}]),
        ]
    )
    assert [(i["reviewer"], i["severity"]) for i in panel.issues] == [("opus", "high"), ("qwen", "low")]
    assert [i["reviewer"] for i in panel.high_severity_issues] == ["opus"]


def test_member_issues_are_not_mutated():
    issues = [{"severity": "low", "description": "x"}]
    panel = ReviewPanelResult([_member("opus", 1, 90, issues=issues)])
    _ = panel.issues
    assert "reviewer" not in issues[0]


def test_partial_answer_note_lists_missing_members():
    panel = ReviewPanelResult(
        [_member("opus", 2, 90), _member("qwen", 1, 80), _member("deepseek", 1, error="нет DEEPSEEK_API_KEY")]
    )
    notes = panel.panel_notes
    assert "second reviewer: 2 of 3 members answered (75% by weight)" in notes
    assert "second reviewer · deepseek: did not answer (нет DEEPSEEK_API_KEY)" in notes


@pytest.mark.parametrize(
    ("low", "expect_note"),
    [(90 - DISAGREEMENT_SPREAD, True), (90 - DISAGREEMENT_SPREAD + 1, False)],
)
def test_disagreement_note_threshold(low, expect_note):
    panel = ReviewPanelResult([_member("opus", 2, 90), _member("qwen", 1, low)])
    has_note = any("disagree" in n for n in panel.panel_notes)
    assert has_note is expect_note


def test_disagreement_note_names_scores():
    panel = ReviewPanelResult([_member("opus", 2, 90), _member("qwen", 1, 45)])
    assert "reviewers disagree: opus 90, qwen 45; read their findings" in panel.panel_notes


def test_consistency_notes_are_prefixed_with_label():
    panel = ReviewPanelResult(
        [_member("opus", 2, 90), _member("qwen", 1, 100, issues=[{"severity": "high", "description": "x"}])]
    )
    assert panel.consistency_notes == [
        "second_reviewer · qwen: confidence 100 lowered to 40: the reviewer itself reported an issue with severity high"
    ]


def test_single_member_panel_behaves_like_plain_reviewer():
    plain = SecondReviewResult(confidence=80, issues=[{"severity": "medium", "description": "x"}])
    panel = as_panel(plain)
    assert panel.confidence == 80
    assert panel.sub_score == plain.sub_score == 75.0
    assert panel.consistency_notes == [plain.consistency_note]
    assert panel.panel_notes == []


def test_single_failed_member_keeps_original_error_text():
    plain = SecondReviewResult(error="AI-провайдер недоступен: не задана переменная окружения OPENAI_API_KEY")
    assert as_panel(plain).error == plain.error


def test_as_panel_is_idempotent():
    panel = ReviewPanelResult([_member("opus", 1, 90)])
    assert as_panel(panel) is panel


def test_plain_reviewer_coverage_fraction_and_notes():
    assert SecondReviewResult(confidence=50).coverage_fraction == 1.0
    assert SecondReviewResult(error="x").coverage_fraction == 0.0
    assert SecondReviewResult(confidence=50).consistency_notes == []


def _other_checks():
    pt = PropertyTestCheckResult(results=[FunctionCheckResult("f", "a.py", "passed", True)])
    sd = SemanticDiffCheckResult(results=[SemanticChangeResult("f", "a.py", 100, [])])
    return pt, sd


def test_partial_panel_lowers_evidence_cap():
    pt, sd = _other_checks()
    panel = ReviewPanelResult([_member("opus", 2, 100), _member("qwen", 1, 100), _member("deepseek", 1, error="429")])
    score = compute_score(pt, sd, panel, Config())
    assert score.evidence_coverage == pytest.approx(0.9125, abs=1e-3)
    assert score.overall == pytest.approx(95.6, abs=0.1)
    assert "second reviewer: 2 of 3 members answered (75% by weight)" in score.notes


def test_full_panel_is_not_capped():
    pt, sd = _other_checks()
    panel = ReviewPanelResult([_member("opus", 2, 100), _member("qwen", 1, 100)])
    score = compute_score(pt, sd, panel, Config())
    assert score.evidence_cap is None
    assert score.overall == 100.0


def test_panel_score_enters_overall_with_full_reviewer_weight():
    pt, sd = _other_checks()
    panel = ReviewPanelResult([_member("opus", 2, 90), _member("qwen", 1, 60)])
    score = compute_score(pt, sd, panel, Config())
    assert score.sub_scores["second_reviewer"] == pytest.approx(80.0)
    assert score.overall == pytest.approx(93.0)


def test_disagreement_note_reaches_score_notes():
    pt, sd = _other_checks()
    panel = ReviewPanelResult([_member("opus", 2, 95), _member("qwen", 1, 40)])
    score = compute_score(pt, sd, panel, Config())
    assert any("disagree" in n for n in score.notes)


def test_panel_where_nobody_answered_is_not_run():
    pt, sd = _other_checks()
    panel = ReviewPanelResult(
        [_member("opus", 2, error="нет ANTHROPIC_API_KEY"), _member("qwen", 1, error="Ollama недоступна")]
    )
    score = compute_score(pt, sd, panel, Config())
    assert score.sub_scores["second_reviewer"] is None
    assert "second_reviewer: did not run (opus: нет ANTHROPIC_API_KEY; qwen: Ollama недоступна)" in score.notes
