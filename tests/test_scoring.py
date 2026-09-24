from confidence_scorer.checks.property_tests import FunctionCheckResult, PropertyTestCheckResult
from confidence_scorer.checks.second_reviewer import SecondReviewResult
from confidence_scorer.checks.semantic_diff import SemanticChangeResult, SemanticDiffCheckResult
from confidence_scorer.config import Config
from confidence_scorer.scoring import compute_score


def _empty_checks():
    return PropertyTestCheckResult(), SemanticDiffCheckResult(), SecondReviewResult()


def test_all_checks_unavailable_gives_unknown_verdict():
    pt, sd, rv = _empty_checks()
    score = compute_score(pt, sd, rv, Config())
    assert score.overall is None
    assert score.verdict_key == "unknown"


def test_weights_redistribute_when_some_checks_missing():
    pt = PropertyTestCheckResult(results=[FunctionCheckResult("f", "a.py", "passed", True)])
    sd = SemanticDiffCheckResult()
    rv = SecondReviewResult(confidence=80)
    score = compute_score(pt, sd, rv, Config())
    assert score.sub_scores["property_tests"] == 100.0
    assert score.sub_scores["semantic_diff"] is None
    assert abs(sum(score.weights_used.values()) - 1.0) < 1e-9
    assert "semantic_diff" not in score.weights_used


def test_hard_fail_caps_score_regardless_of_other_checks():
    pt = PropertyTestCheckResult(
        results=[
            FunctionCheckResult(
                "f", "a.py", "failed", is_public=True, reason="mismatch",
                kwargs={"x": "1"}, old_repr="1", new_repr="2",
            )
        ]
    )
    sd = SemanticDiffCheckResult(results=[SemanticChangeResult("f", "a.py", 100, [])])
    rv = SecondReviewResult(confidence=100, verdict="looks fine")
    cfg = Config()
    score = compute_score(pt, sd, rv, cfg)
    assert score.hard_fail is True
    assert score.overall <= cfg.hard_fail.cap_score_on_confirmed_counterexample


def test_hard_fail_ignored_for_private_function():
    pt = PropertyTestCheckResult(
        results=[FunctionCheckResult("_helper", "a.py", "failed", is_public=False, reason="mismatch")]
    )
    sd, rv = SemanticDiffCheckResult(), SecondReviewResult()
    score = compute_score(pt, sd, rv, Config())
    assert score.hard_fail is False


def test_verdict_bands_boundaries():
    cfg = Config()
    cfg.evidence.enabled = False
    pt = PropertyTestCheckResult()
    rv = SecondReviewResult()
    for value, expected in [(100, "pass"), (85, "pass"), (84, "warn"), (65, "warn"), (64, "manual"), (40, "manual"), (39, "fail"), (0, "fail")]:
        sd = SemanticDiffCheckResult(results=[SemanticChangeResult("f", "a.py", value, [])])
        score = compute_score(pt, sd, rv, cfg)
        assert score.overall == value
        assert score.verdict_key == expected, (value, score.verdict_key)


def test_single_check_cannot_yield_full_confidence():
    pt = PropertyTestCheckResult(results=[FunctionCheckResult("f", "a.py", "passed", True)])
    sd, rv = SemanticDiffCheckResult(), SecondReviewResult()
    cfg = Config()

    score = compute_score(pt, sd, rv, cfg)

    assert score.sub_scores["property_tests"] == 100.0
    assert score.overall < 100.0
    assert score.verdict_key != "pass"
    assert score.evidence_coverage == cfg.weights.property_tests
    assert score.evidence_cap is not None
    assert any("trust ceiling" in note for note in score.notes)


def test_full_coverage_is_not_capped():
    pt = PropertyTestCheckResult(results=[FunctionCheckResult("f", "a.py", "passed", True)])
    sd = SemanticDiffCheckResult(results=[SemanticChangeResult("f", "a.py", 100, [])])
    rv = SecondReviewResult(confidence=100)

    score = compute_score(pt, sd, rv, Config())

    assert score.overall == 100.0
    assert score.verdict_key == "pass"
    assert score.evidence_coverage == 1.0
    assert score.evidence_cap is None


def test_technical_errors_do_not_count_as_failures():
    pt = PropertyTestCheckResult(
        results=[
            FunctionCheckResult("ok", "a.py", "passed", True),
            FunctionCheckResult("broken", "a.py", "error", True, reason="exec failed: ModuleNotFoundError"),
        ]
    )
    assert pt.sub_score == 100.0
    assert any("could not be checked for technical reasons" in note for note in pt.notes)


def test_only_errors_means_check_did_not_run():
    pt = PropertyTestCheckResult(
        results=[FunctionCheckResult("broken", "a.py", "error", True, reason="exec failed")]
    )
    assert pt.sub_score is None
