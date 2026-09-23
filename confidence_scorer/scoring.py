from __future__ import annotations

from dataclasses import dataclass, field

from confidence_scorer.checks.property_tests import PropertyTestCheckResult
from confidence_scorer.checks.review_panel import ReviewPanelResult, as_panel
from confidence_scorer.checks.second_reviewer import SecondReviewResult
from confidence_scorer.checks.semantic_diff import SemanticDiffCheckResult
from confidence_scorer.config import Config

VERDICT_TEXT = {
    "hard_fail": "Найден подтверждённый контрпример поведения: не мержить без ручной проверки",
    "fail": "Низкая уверенность, не мержить без ревью",
    "manual": "Низкая или умеренная уверенность, нужна ручная проверка",
    "warn": "Умеренная уверенность, рекомендуется ревью перед мержем",
    "pass": "Высокая уверенность, можно мержить",
    "unknown": "Недостаточно данных: ни одна проверка не смогла отработать",
}


@dataclass
class ConfidenceScore:
    overall: float | None
    verdict_key: str
    verdict_text: str
    sub_scores: dict[str, float | None]
    weights_used: dict[str, float]
    hard_fail: bool
    hard_fail_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence_coverage: float = 1.0
    evidence_cap: float | None = None


def _verdict_key(score: float, thresholds, hard_fail: bool) -> str:
    if hard_fail:
        return "hard_fail"
    if score >= thresholds.pass_at:
        return "pass"
    if score >= thresholds.warn_below:
        return "warn"
    if score >= thresholds.fail_below:
        return "manual"
    return "fail"


def compute_score(
    property_result: PropertyTestCheckResult,
    semantic_result: SemanticDiffCheckResult,
    review_result: SecondReviewResult | ReviewPanelResult,
    config: Config,
    extra_notes: list[str] | None = None,
) -> ConfidenceScore:
    review = as_panel(review_result)
    sub_scores = {
        "property_tests": property_result.sub_score,
        "semantic_diff": semantic_result.sub_score,
        "second_reviewer": review.sub_score,
    }
    weights = {
        "property_tests": config.weights.property_tests,
        "semantic_diff": config.weights.semantic_diff,
        "second_reviewer": config.weights.second_reviewer,
    }

    notes: list[str] = list(extra_notes or [])
    if property_result.sub_score is None:
        notes.append("property_tests: не выполнялись (нет подходящих изменённых функций, либо выполнение кода выключено)")
    if semantic_result.sub_score is None:
        notes.append(f"semantic_diff: не выполнялся ({semantic_result.skip_reason})")
    if review.sub_score is None:
        notes.append(f"second_reviewer: не выполнялся ({review.error or 'нет данных'})")
    notes.extend(property_result.notes)
    notes.extend(semantic_result.consistency_notes)
    notes.extend(review.consistency_notes)
    notes.extend(review.panel_notes)

    available = {k: v for k, v in sub_scores.items() if v is not None}

    if not available:
        return ConfidenceScore(
            overall=None,
            verdict_key="unknown",
            verdict_text=VERDICT_TEXT["unknown"],
            sub_scores=sub_scores,
            weights_used={},
            hard_fail=False,
            notes=notes,
            evidence_coverage=0.0,
        )

    total_weight = sum(weights[k] for k in available) or 1.0
    normalized_weights = {k: weights[k] / total_weight for k in available}
    overall = sum(available[k] * normalized_weights[k] for k in available)

    all_weight = sum(weights.values()) or 1.0
    completeness = {
        "property_tests": 1.0,
        "semantic_diff": 1.0,
        "second_reviewer": review.coverage_fraction,
    }
    coverage = sum(weights[k] * completeness[k] for k in available) / all_weight
    evidence_cap: float | None = None
    if config.evidence.enabled and coverage < 1.0:
        min_cap = float(config.evidence.min_cap)
        evidence_cap = min_cap + (100.0 - min_cap) * coverage
        if overall > evidence_cap:
            notes.append(
                f"score ограничен {evidence_cap:.0f} из-за неполного покрытия проверками "
                f"(отработало {coverage * 100:.0f}% веса проверок). Это потолок доверия, "
                f"а не оценка качества кода"
            )
            overall = evidence_cap

    hard_fail = False
    hard_fail_reasons = list(property_result.hard_fail_reasons)
    if config.hard_fail.enabled and hard_fail_reasons:
        hard_fail = True
        overall = min(overall, float(config.hard_fail.cap_score_on_confirmed_counterexample))

    overall = max(0.0, min(100.0, overall))
    key = _verdict_key(overall, config.thresholds, hard_fail)

    return ConfidenceScore(
        overall=round(overall, 1),
        verdict_key=key,
        verdict_text=VERDICT_TEXT[key],
        sub_scores=sub_scores,
        weights_used=normalized_weights,
        hard_fail=hard_fail,
        hard_fail_reasons=hard_fail_reasons,
        notes=notes,
        evidence_coverage=round(coverage, 3),
        evidence_cap=round(evidence_cap, 1) if evidence_cap is not None else None,
    )
