from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from confidence_scorer.ai.base import AIProvider
from confidence_scorer.checks.second_reviewer import SecondReviewResult, run_second_review
from confidence_scorer.config import Config, ReviewerConfig
from confidence_scorer.git_diff import ChangedFile
from confidence_scorer.i18n import tr

DISAGREEMENT_SPREAD = 40

_SINGLE_REVIEWER_PREFIX = "second_reviewer:"


@dataclass
class PanelMember:
    label: str
    weight: float
    result: SecondReviewResult
    provider: str | None = None
    model: str | None = None

    @property
    def score(self) -> float | None:
        return self.result.sub_score

    @property
    def answered(self) -> bool:
        return self.score is not None


@dataclass
class ReviewPanelResult:
    members: list[PanelMember] = field(default_factory=list)

    @property
    def answered(self) -> list[PanelMember]:
        return [m for m in self.members if m.answered]

    @property
    def sub_score(self) -> float | None:
        answered = self.answered
        total = sum(m.weight for m in answered)
        if not answered or total <= 0:
            return None
        return sum(m.weight * m.score for m in answered) / total

    @property
    def confidence(self) -> int | None:
        if len(self.members) == 1:
            return self.members[0].result.confidence
        score = self.sub_score
        return None if score is None else round(score)

    @property
    def answered_share(self) -> float:
        total = sum(m.weight for m in self.members)
        if total <= 0:
            return 0.0
        return sum(m.weight for m in self.answered) / total

    @property
    def coverage_fraction(self) -> float:
        return self.answered_share

    def _lead(self) -> PanelMember | None:
        answered = self.answered
        return max(answered, key=lambda m: m.weight) if answered else None

    @property
    def verdict(self) -> str | None:
        lead = self._lead()
        return lead.result.verdict if lead else None

    @property
    def summary(self) -> str | None:
        lead = self._lead()
        return lead.result.summary if lead else None

    @property
    def issues(self) -> list[dict]:
        return [{**issue, "reviewer": m.label} for m in self.members for issue in m.result.issues]

    @property
    def high_severity_issues(self) -> list[dict]:
        return [i for i in self.issues if i.get("severity") == "high"]

    @property
    def error(self) -> str | None:
        if self.answered:
            return None
        if len(self.members) == 1:
            return self.members[0].result.error
        no_data = tr("no data", "нет данных")
        return "; ".join(f"{m.label}: {m.result.error or no_data}" for m in self.members)

    @property
    def consistency_notes(self) -> list[str]:
        if len(self.members) == 1:
            return self.members[0].result.consistency_notes
        return [
            note.replace(_SINGLE_REVIEWER_PREFIX, f"second_reviewer · {m.label}:", 1)
            for m in self.members
            for note in m.result.consistency_notes
        ]

    @property
    def panel_notes(self) -> list[str]:
        notes: list[str] = []
        answered = self.answered
        if answered and len(answered) < len(self.members):
            notes.append(
                tr(
                    f"second reviewer: {len(answered)} of {len(self.members)} members answered "
                    f"({self.answered_share * 100:.0f}% by weight)",
                    f"второй ревьюер: ответили {len(answered)} из {len(self.members)} участников "
                    f"({self.answered_share * 100:.0f}% по весу)",
                )
            )
            no_data = tr("no data", "нет данных")
            notes.extend(
                tr(
                    f"second reviewer · {m.label}: did not answer ({m.result.error or no_data})",
                    f"второй ревьюер · {m.label}: не ответил ({m.result.error or no_data})",
                )
                for m in self.members
                if not m.answered
            )
        if len(answered) >= 2:
            scores = [m.score for m in answered]
            if max(scores) - min(scores) >= DISAGREEMENT_SPREAD:
                listing = ", ".join(f"{m.label} {m.score:.0f}" for m in answered)
                notes.append(
                    tr(
                        f"reviewers disagree: {listing}; read their findings",
                        f"ревьюеры разошлись: {listing}; посмотрите их замечания",
                    )
                )
        return notes


def as_panel(review: SecondReviewResult | ReviewPanelResult) -> ReviewPanelResult:
    if isinstance(review, ReviewPanelResult):
        return review
    return ReviewPanelResult(members=[PanelMember(label="second_reviewer", weight=1.0, result=review)])


def run_review_panel(
    diff_text: str,
    changed_files: list[ChangedFile],
    members: list[tuple[ReviewerConfig, AIProvider | None]],
    config: Config,
) -> ReviewPanelResult:
    if not members:
        return ReviewPanelResult()

    max_workers = max(1, min(config.limits.max_parallel_ai_calls, len(members)))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="confidence-scorer-review") as pool:
        results = list(
            pool.map(lambda pair: run_second_review(diff_text, changed_files, pair[1], config), members)
        )

    return ReviewPanelResult(
        members=[
            PanelMember(
                label=cfg.display_label,
                weight=cfg.weight,
                result=result,
                provider=cfg.provider,
                model=cfg.model,
            )
            for (cfg, _), result in zip(members, results, strict=True)
        ]
    )
