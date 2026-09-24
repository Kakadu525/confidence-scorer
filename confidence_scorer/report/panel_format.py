from __future__ import annotations

from confidence_scorer.checks.review_panel import PanelMember, ReviewPanelResult
from confidence_scorer.checks.severity import worst_severity
from confidence_scorer.i18n import tr

DETAIL_LIMIT = 300


def clip(text: str, limit: int = DETAIL_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def member_score_text(member: PanelMember) -> str:
    score = member.score
    if score is None:
        return "-"
    raw = member.result.confidence
    if raw is not None and score < raw:
        cap = tr("cap", "потолок")
        return f"{raw} → {score:.0f} ({cap}: {worst_severity(member.result.issues)})"
    return f"{score:.0f}"


def member_status_text(member: PanelMember) -> str:
    return "✓" if member.answered else (member.result.error or tr("no answer", "нет ответа"))


def panel_summary_text(panel: ReviewPanelResult) -> str | None:
    if panel.sub_score is None:
        return None
    return tr(
        f"Panel: {panel.sub_score:.0f}, {panel.answered_share * 100:.0f}% answered by weight",
        f"Панель: {panel.sub_score:.0f}, ответили {panel.answered_share * 100:.0f}% по весу",
    )
