from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from confidence_scorer.checks.review_panel import as_panel
from confidence_scorer.i18n import tr
from confidence_scorer.pipeline import PipelineResult
from confidence_scorer.report.panel_format import clip, member_score_text, member_status_text, panel_summary_text

VERDICT_STYLE = {
    "pass": "bold green",
    "warn": "bold yellow",
    "manual": "bold yellow",
    "fail": "bold red",
    "hard_fail": "bold red",
    "unknown": "bold white",
}

def _check_label(key: str) -> str:
    return {
        "property_tests": "Property-based tests (differential)",
        "semantic_diff": "Semantic diff (AI)",
        "second_reviewer": tr("Second AI reviewer", "Второй AI-ревьюер"),
    }[key]


def _fmt_score(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f}"


def render_terminal_report(result: PipelineResult, console: Console | None = None) -> None:
    console = console or Console()
    score = result.score
    style = VERDICT_STYLE.get(score.verdict_key, "bold white")

    header = Text()
    header.append("Confidence Score: ", style="bold")
    header.append(f"{score.overall:.0f}/100\n" if score.overall is not None else "N/A\n", style=style)
    header.append(score.verdict_text, style=style)
    if score.evidence_cap is not None:
        header.append(
            tr(
                f"\nCheck coverage: {score.evidence_coverage * 100:.0f}%, "
                f"score cap {score.evidence_cap:.0f}",
                f"\nПокрытие проверками: {score.evidence_coverage * 100:.0f}%, "
                f"потолок score {score.evidence_cap:.0f}",
            ),
            style="dim",
        )
    console.print(Panel(header, title="confidence-scorer", expand=False))

    table = Table(title=tr("Breakdown by check", "Разбивка по проверкам"))
    table.add_column(tr("Check", "Проверка"))
    table.add_column("Sub-score", justify="right")
    table.add_column(tr("Weight", "Вес"), justify="right")
    for key in ("property_tests", "semantic_diff", "second_reviewer"):
        weight = score.weights_used.get(key)
        table.add_row(
            _check_label(key),
            _fmt_score(score.sub_scores.get(key)),
            f"{weight * 100:.0f}%" if weight is not None else "-",
        )
    console.print(table)

    pt = result.property_result
    pt_table = Table(title=f"Property-based tests: {pt.passed_count} passed / {pt.failed_count} failed / {pt.error_count} error / {pt.skipped_count} skipped")
    pt_table.add_column(tr("Function", "Функция"))
    pt_table.add_column(tr("File", "Файл"))
    pt_table.add_column(tr("Status", "Статус"))
    pt_table.add_column(tr("Details", "Детали"))
    for r in pt.results:
        status_style = {"passed": "green", "failed": "bold red", "error": "yellow", "skipped": "dim"}.get(r.status, "")
        detail = r.reason or ""
        if r.status == "failed":
            detail = tr(
                f"input={r.kwargs}, before={r.old_repr}, after={r.new_repr}",
                f"вход={r.kwargs}, было={r.old_repr}, стало={r.new_repr}",
            )
        pt_table.add_row(r.qualname, r.file_path, Text(r.status, style=status_style), clip(detail))
    if pt.results:
        console.print(pt_table)

    if result.file_issues:
        issues_table = Table(title=tr("Files that could not be analyzed", "Файлы, которые не удалось проанализировать"))
        issues_table.add_column(tr("File", "Файл"))
        issues_table.add_column(tr("Language", "Язык"))
        issues_table.add_column(tr("Reason", "Причина"))
        for issue in result.file_issues:
            issues_table.add_row(issue.path, issue.language, clip(issue.reason))
        console.print(issues_table)

    if score.hard_fail_reasons:
        console.print(Panel("\n".join(score.hard_fail_reasons), title=tr("Confirmed counterexamples (hard fail)", "Подтверждённые контрпримеры (hard fail)"), style="bold red"))

    notable = result.semantic_result.notable_changes
    if notable:
        sem_table = Table(title=tr("Semantic diff: notable behavior changes", "Semantic diff: заметные изменения поведения"))
        sem_table.add_column(tr("Function", "Функция"))
        sem_table.add_column("Severity")
        sem_table.add_column(tr("Description", "Описание"))
        for qualname, change in notable:
            sem_table.add_row(qualname, str(change.get("severity")), str(change.get("description"))[:120])
        console.print(sem_table)

    review = as_panel(result.review_result)
    is_panel = len(review.members) > 1
    if is_panel:
        panel_table = Table(title=tr("Second AI reviewer: panel", "Второй AI-ревьюер: панель"))
        panel_table.add_column(tr("Reviewer", "Ревьюер"))
        panel_table.add_column(tr("Weight", "Вес"), justify="right")
        panel_table.add_column(tr("Score", "Оценка"))
        panel_table.add_column(tr("Status", "Статус"))
        for member in review.members:
            panel_table.add_row(
                member.label, f"{member.weight:g}", member_score_text(member), member_status_text(member)[:80]
            )
        console.print(panel_table)
        summary_line = panel_summary_text(review)
        if summary_line:
            console.print(summary_line)

    if review.issues:
        rv_table = Table(title=tr("Second AI reviewer: findings", "Второй AI-ревьюер: замечания"))
        rv_table.add_column("Severity")
        rv_table.add_column(tr("File", "Файл"))
        rv_table.add_column(tr("Description", "Описание"))
        if is_panel:
            rv_table.add_column(tr("Found by", "Кто нашёл"))
        for issue in review.issues:
            row = [str(issue.get("severity")), str(issue.get("file", "")), str(issue.get("description"))[:120]]
            if is_panel:
                row.append(str(issue.get("reviewer", "")))
            rv_table.add_row(*row)
        console.print(rv_table)
    if review.summary:
        console.print(Panel(review.summary, title=tr("Second reviewer summary", "Резюме второго ревьюера")))

    if score.notes:
        console.print(Panel("\n".join(f"• {n}" for n in score.notes), title=tr("Notes", "Примечания"), style="dim"))
