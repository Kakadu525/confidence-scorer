from __future__ import annotations

import pytest

from confidence_scorer.checks.property_tests import PropertyTestCheckResult
from confidence_scorer.checks.second_reviewer import SecondReviewResult
from confidence_scorer.checks.semantic_diff import SemanticChangeResult, SemanticDiffCheckResult
from confidence_scorer.checks.severity import SEVERITY_CEILINGS, apply_ceiling, severity_ceiling
from confidence_scorer.config import Config
from confidence_scorer.scoring import compute_score


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([], None),
        ([{"severity": "low"}], None),
        ([{"severity": "medium"}], SEVERITY_CEILINGS["medium"]),
        ([{"severity": "medium"}, {"severity": "high"}], SEVERITY_CEILINGS["high"]),
        ([{"severity": "HIGH"}], SEVERITY_CEILINGS["high"]),
        (["не словарь", {"без severity": 1}], None),
    ],
)
def test_severity_ceiling(items, expected):
    assert severity_ceiling(items) == expected


def test_low_score_is_not_raised_by_ceiling():
    assert apply_ceiling(10, [{"severity": "medium"}]) == 10


def test_contradictory_reviewer_is_capped_and_explained():
    review = SecondReviewResult(confidence=100, issues=[{"severity": "high", "description": "удалена проверка"}])

    assert review.sub_score == SEVERITY_CEILINGS["high"]
    assert "понижен" in review.consistency_note


def test_consistent_reviewer_is_untouched():
    review = SecondReviewResult(confidence=90, issues=[{"severity": "low", "description": "мелочь"}])

    assert review.sub_score == 90.0
    assert review.consistency_note is None


def test_semantic_diff_caps_each_function_separately():
    result = SemanticDiffCheckResult(
        results=[
            SemanticChangeResult("safe", "a.py", 100, []),
            SemanticChangeResult("risky", "a.py", 95, [{"description": "граница", "severity": "high"}]),
        ]
    )
    assert result.sub_score == pytest.approx(70.0)
    assert len(result.consistency_notes) == 1


def test_contradiction_cannot_produce_pass_verdict():
    review = SecondReviewResult(confidence=100, issues=[{"severity": "high", "description": "удалена проверка"}])
    semantic = SemanticDiffCheckResult(results=[SemanticChangeResult("f", "a.py", 100, [])])

    score = compute_score(PropertyTestCheckResult(), semantic, review, Config())

    assert score.verdict_key != "pass"
    assert any("понижен" in note for note in score.notes)


def test_notes_name_the_actual_severity():
    review = SecondReviewResult(confidence=95, issues=[{"severity": "medium", "description": "x"}])
    assert "severity medium" in review.consistency_note

    semantic = SemanticDiffCheckResult(
        results=[SemanticChangeResult("f", "a.py", 90, [{"description": "x", "severity": "medium"}])]
    )
    assert "severity medium" in semantic.consistency_notes[0]
