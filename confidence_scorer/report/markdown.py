from __future__ import annotations

from confidence_scorer.checks.review_panel import as_panel
from confidence_scorer.pipeline import PipelineResult
from confidence_scorer.report.panel_format import clip, member_score_text, member_status_text, panel_summary_text

VERDICT_EMOJI = {
    "pass": "🟢",
    "warn": "🟡",
    "manual": "🟠",
    "fail": "🔴",
    "hard_fail": "🔴",
    "unknown": "⚪",
}

CHECK_LABELS = {
    "property_tests": "Property-based tests (differential)",
    "semantic_diff": "Semantic diff (AI)",
    "second_reviewer": "Второй AI-ревьюер",
}


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f}"


def render_markdown_report(result: PipelineResult, comment_marker: str = "") -> str:
    score = result.score
    emoji = VERDICT_EMOJI.get(score.verdict_key, "⚪")
    lines = []
    if comment_marker:
        lines.append(comment_marker)

    overall = f"{score.overall:.0f}/100" if score.overall is not None else "N/A"
    lines.append(f"## {emoji} Confidence Score: {overall}")
    lines.append("")
    lines.append(f"**{score.verdict_text}**")
    lines.append("")
    if score.evidence_cap is not None:
        lines.append(
            f"> Покрытие проверками: **{score.evidence_coverage * 100:.0f}%**, "
            f"потолок score ограничен **{score.evidence_cap:.0f}**."
        )
        lines.append("")
    lines.append("| Проверка | Sub-score | Вес |")
    lines.append("|---|---|---|")
    for key in ("property_tests", "semantic_diff", "second_reviewer"):
        weight = score.weights_used.get(key)
        w = f"{weight * 100:.0f}%" if weight is not None else "-"
        lines.append(f"| {CHECK_LABELS[key]} | {_fmt(score.sub_scores.get(key))} | {w} |")
    lines.append("")

    pt = result.property_result
    lines.append(
        f"<details><summary>Property-based tests: {pt.passed_count} passed / "
        f"{pt.failed_count} failed / {pt.error_count} error / {pt.skipped_count} skipped</summary>\n"
    )
    if pt.results:
        lines.append("| Функция | Файл | Статус | Детали |")
        lines.append("|---|---|---|---|")
        for r in pt.results:
            detail = r.reason or ""
            if r.status == "failed":
                detail = f"вход=`{r.kwargs}`, было=`{r.old_repr}`, стало=`{r.new_repr}`"
            detail = clip((detail or "").replace("|", "\\|"))
            lines.append(f"| `{r.qualname}` | `{r.file_path}` | {r.status} | {detail} |")
    else:
        lines.append("_Изменённых функций для differential testing не найдено._")
    lines.append("\n</details>\n")

    if result.file_issues:
        lines.append("### Файлы, которые не удалось проанализировать")
        for issue in result.file_issues:
            lines.append(f"- `{issue.path}` ({issue.language}): {issue.reason}")
        lines.append("")

    if score.hard_fail_reasons:
        lines.append("### Подтверждённые контрпримеры поведения")
        for reason in score.hard_fail_reasons:
            lines.append(f"- {reason}")
        lines.append("")

    notable = result.semantic_result.notable_changes
    if notable:
        lines.append("<details><summary>Semantic diff: заметные изменения поведения</summary>\n")
        lines.append("| Функция | Severity | Описание |")
        lines.append("|---|---|---|")
        for qualname, change in notable:
            desc = clip(str(change.get("description", "")).replace("|", "\\|"))
            lines.append(f"| `{qualname}` | {change.get('severity')} | {desc} |")
        lines.append("\n</details>\n")

    review = as_panel(result.review_result)
    is_panel = len(review.members) > 1
    if review.issues or review.summary or is_panel:
        lines.append("<details><summary>Второй AI-ревьюер</summary>\n")
        if is_panel:
            lines.append("| Ревьюер | Вес | Оценка | Статус |")
            lines.append("|---|---|---|---|")
            for member in review.members:
                status = member_status_text(member).replace("|", "\\|")[:120]
                lines.append(f"| {member.label} | {member.weight:g} | {member_score_text(member)} | {status} |")
            summary_line = panel_summary_text(review)
            if summary_line:
                lines.append(f"\n{summary_line}\n")
        if review.summary:
            lines.append(f"_{review.summary}_\n")
        if review.issues:
            header = "| Severity | Файл | Описание |" + (" Кто нашёл |" if is_panel else "")
            lines.append(header)
            lines.append("|---|---|---|" + ("---|" if is_panel else ""))
            for issue in review.issues:
                desc = clip(str(issue.get("description", "")).replace("|", "\\|"))
                row = f"| {issue.get('severity')} | `{issue.get('file', '')}` | {desc} |"
                if is_panel:
                    row += f" {issue.get('reviewer', '')} |"
                lines.append(row)
        lines.append("\n</details>\n")

    if score.notes:
        lines.append("<details><summary>Примечания</summary>\n")
        for n in score.notes:
            lines.append(f"- {n}")
        lines.append("\n</details>\n")

    lines.append("\n<sub>Сгенерировано [confidence-scorer](https://github.com/Kakadu525/confidence-scorer). Доверяйте, но проверяйте.</sub>")

    return "\n".join(lines)
