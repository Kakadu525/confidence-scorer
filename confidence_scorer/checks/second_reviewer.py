from __future__ import annotations

from dataclasses import dataclass, field

from confidence_scorer.ai.base import DEFAULT_MAX_TOKENS, AIProvider, failure_reason, unavailable_reason
from confidence_scorer.ai.prompts import second_reviewer_prompt
from confidence_scorer.checks.severity import apply_ceiling, severity_ceiling, worst_severity
from confidence_scorer.config import Config
from confidence_scorer.git_diff import ChangedFile
from confidence_scorer.i18n import tr


@dataclass
class SecondReviewResult:
    confidence: int | None = None
    verdict: str | None = None
    issues: list[dict] = field(default_factory=list)
    summary: str | None = None
    error: str | None = None

    @property
    def sub_score(self) -> float | None:
        if self.confidence is None:
            return None
        return float(apply_ceiling(self.confidence, self.issues))

    @property
    def consistency_note(self) -> str | None:
        ceiling = severity_ceiling(self.issues)
        if self.confidence is None or ceiling is None or self.confidence <= ceiling:
            return None
        return tr(
            f"second_reviewer: confidence {self.confidence} lowered to {ceiling}: "
            f"the reviewer itself reported an issue with severity {worst_severity(self.issues)}",
            f"second_reviewer: confidence {self.confidence} понижен до {ceiling}: "
            f"ревьюер сам сообщил о проблеме с severity {worst_severity(self.issues)}",
        )

    @property
    def consistency_notes(self) -> list[str]:
        note = self.consistency_note
        return [note] if note else []

    @property
    def coverage_fraction(self) -> float:
        return 1.0 if self.sub_score is not None else 0.0

    @property
    def high_severity_issues(self) -> list[dict]:
        return [i for i in self.issues if i.get("severity") == "high"]


def _files_summary(changed_files: list[ChangedFile]) -> str:
    status_names = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
    lines = [f"- {cf.path} ({status_names.get(cf.status, cf.status)})" for cf in changed_files]
    return "\n".join(lines) if lines else "(no changed files in supported languages)"


def run_second_review(
    diff_text: str,
    changed_files: list[ChangedFile],
    provider: AIProvider | None,
    config: Config,
) -> SecondReviewResult:
    if provider is None or not provider.available:
        return SecondReviewResult(error=unavailable_reason(provider))

    max_bytes = config.limits.max_full_diff_bytes
    encoded = diff_text.encode("utf-8")
    if len(encoded) > max_bytes:
        diff_text = encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n... (diff truncated at the size limit)"

    system, user = second_reviewer_prompt(diff_text, _files_summary(changed_files))
    raw = provider.complete_json(system, user, max_tokens=DEFAULT_MAX_TOKENS)

    if not isinstance(raw, dict) or "confidence" not in raw:
        return SecondReviewResult(error=failure_reason(provider) or tr("the AI returned an invalid answer", "AI вернул некорректный ответ"))

    try:
        confidence = max(0, min(100, int(raw["confidence"])))
    except (TypeError, ValueError):
        return SecondReviewResult(error=tr("confidence is not a number", "confidence не число"))

    issues = raw.get("issues")
    issues = [i for i in issues if isinstance(i, dict) and "description" in i] if isinstance(issues, list) else []

    return SecondReviewResult(
        confidence=confidence,
        verdict=raw.get("verdict") if isinstance(raw.get("verdict"), str) else None,
        issues=issues,
        summary=raw.get("summary") if isinstance(raw.get("summary"), str) else None,
    )
