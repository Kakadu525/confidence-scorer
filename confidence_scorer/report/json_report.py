from __future__ import annotations

from dataclasses import asdict

from confidence_scorer.checks.review_panel import as_panel
from confidence_scorer.pipeline import PipelineResult


def render_json_report(result: PipelineResult) -> dict:
    score = result.score
    review = as_panel(result.review_result)
    return {
        "score": {
            "overall": score.overall,
            "verdict": score.verdict_key,
            "verdict_text": score.verdict_text,
            "sub_scores": score.sub_scores,
            "weights_used": score.weights_used,
            "hard_fail": score.hard_fail,
            "hard_fail_reasons": score.hard_fail_reasons,
            "notes": score.notes,
            "evidence_coverage": score.evidence_coverage,
            "evidence_cap": score.evidence_cap,
        },
        "changed_files": [
            {"path": cf.path, "status": cf.status, "language": cf.language} for cf in result.changed_files
        ],
        "file_issues": [asdict(issue) for issue in result.file_issues],
        "property_tests": {
            "passed": result.property_result.passed_count,
            "failed": result.property_result.failed_count,
            "error": result.property_result.error_count,
            "skipped": result.property_result.skipped_count,
            "results": [asdict(r) for r in result.property_result.results],
        },
        "semantic_diff": {
            "results": [asdict(r) for r in result.semantic_result.results],
        },
        "second_reviewer": {
            "confidence": review.confidence,
            "verdict": review.verdict,
            "summary": review.summary,
            "issues": review.issues,
            "error": review.error,
            "answered_share": round(review.answered_share, 3),
            "members": [
                {
                    "label": m.label,
                    "provider": m.provider,
                    "model": m.model,
                    "weight": m.weight,
                    "confidence": m.result.confidence,
                    "score": m.score,
                    "verdict": m.result.verdict,
                    "summary": m.result.summary,
                    "issues": m.result.issues,
                    "error": m.result.error,
                }
                for m in review.members
            ],
        },
    }
