from __future__ import annotations

from confidence_scorer.checks.review_panel import PanelMember, ReviewPanelResult
from confidence_scorer.checks.severity import worst_severity

DETAIL_LIMIT = 300


def clip(text: str, limit: int = DETAIL_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def member_score_text(member: PanelMember) -> str:
    score = member.score
    if score is None:
        return "-"
    raw = member.result.confidence
    if raw is not None and score < raw:
        return f"{raw} → {score:.0f} (потолок: {worst_severity(member.result.issues)})"
    return f"{score:.0f}"


def member_status_text(member: PanelMember) -> str:
    return "✓" if member.answered else (member.result.error or "нет ответа")


def panel_summary_text(panel: ReviewPanelResult) -> str | None:
    if panel.sub_score is None:
        return None
    return f"Панель: {panel.sub_score:.0f}, ответили {panel.answered_share * 100:.0f}% по весу"
