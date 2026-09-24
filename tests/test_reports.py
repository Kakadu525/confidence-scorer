from __future__ import annotations

from rich.console import Console

from confidence_scorer.checks.property_tests import FunctionCheckResult, PropertyTestCheckResult
from confidence_scorer.checks.second_reviewer import SecondReviewResult
from confidence_scorer.checks.semantic_diff import SemanticChangeResult, SemanticDiffCheckResult
from confidence_scorer.config import Config
from confidence_scorer.git_diff import ChangedFile
from confidence_scorer.pipeline import FileIssue, PipelineResult
from confidence_scorer.report.json_report import render_json_report
from confidence_scorer.report.markdown import render_markdown_report
from confidence_scorer.report.terminal import render_terminal_report
from confidence_scorer.scoring import compute_score


def _result(*, with_issues: bool = False, with_failure: bool = False) -> PipelineResult:
    config = Config()
    pt = PropertyTestCheckResult(
        results=[
            FunctionCheckResult("ok", "a.py", "passed", True),
            FunctionCheckResult("skipped_one", "a.py", "skipped", True, reason="нет type hints"),
            FunctionCheckResult("broken", "a.py", "error", True, reason="exec failed"),
        ]
    )
    if with_failure:
        pt.results.append(
            FunctionCheckResult(
                "bad", "a.py", "failed", True, reason="расхождение",
                kwargs={"n": "1"}, old_repr="1", new_repr="2",
            )
        )
    sd = SemanticDiffCheckResult(
        results=[
            SemanticChangeResult(
                "ok", "a.py", 70, [{"description": "изменена | граница", "severity": "high", "category": "logic"}]
            )
        ]
    )
    rv = SecondReviewResult(
        confidence=80,
        verdict="в целом ок",
        issues=[{"severity": "medium", "file": "a.py", "description": "спорный | случай"}],
        summary="Резюме",
    )
    issues = [FileIssue("broken.ts", "javascript", "parse error: Unexpected token")] if with_issues else []
    score = compute_score(pt, sd, rv, config, extra_notes=[f"{i.path}: {i.reason}" for i in issues])
    return PipelineResult(
        config=config,
        changed_files=[ChangedFile("a.py", "M", "old", "new", "python")],
        function_changes=[],
        property_result=pt,
        semantic_result=sd,
        review_result=rv,
        score=score,
        file_issues=issues,
    )


def test_terminal_report_renders_every_section():
    result = _result(with_issues=True, with_failure=True)
    console = Console(record=True, width=200, force_terminal=False)

    render_terminal_report(result, console)
    text = console.export_text()

    assert "Confidence Score" in text
    assert "Property-based tests" in text
    assert "broken.ts" in text
    assert "Confirmed counterexamples" in text
    assert "Резюме" in text


def test_terminal_report_handles_absent_score():
    empty = PropertyTestCheckResult()
    result = PipelineResult(
        config=Config(),
        changed_files=[],
        function_changes=[],
        property_result=empty,
        semantic_result=SemanticDiffCheckResult(),
        review_result=SecondReviewResult(),
        score=compute_score(empty, SemanticDiffCheckResult(), SecondReviewResult(), Config()),
    )
    console = Console(record=True, width=200, force_terminal=False)

    render_terminal_report(result, console)

    assert "N/A" in console.export_text()


def test_markdown_report_escapes_pipes_and_shows_issues():
    markdown = render_markdown_report(_result(with_issues=True, with_failure=True), comment_marker="<!-- m -->")

    assert markdown.startswith("<!-- m -->")
    assert "broken.ts" in markdown
    assert "could not be analyzed" in markdown
    assert "изменена \\| граница" in markdown
    assert "спорный \\| случай" in markdown


def test_markdown_report_reports_evidence_cap():
    pt = PropertyTestCheckResult(results=[FunctionCheckResult("ok", "a.py", "passed", True)])
    result = PipelineResult(
        config=Config(),
        changed_files=[],
        function_changes=[],
        property_result=pt,
        semantic_result=SemanticDiffCheckResult(),
        review_result=SecondReviewResult(),
        score=compute_score(pt, SemanticDiffCheckResult(), SecondReviewResult(), Config()),
    )

    markdown = render_markdown_report(result)

    assert "Check coverage" in markdown


def test_json_report_contains_issues_and_coverage():
    payload = render_json_report(_result(with_issues=True))

    assert payload["file_issues"][0]["path"] == "broken.ts"
    assert 0.0 < payload["score"]["evidence_coverage"] <= 1.0
    assert payload["property_tests"]["error"] == 1
    assert payload["second_reviewer"]["confidence"] == 80


def _result_with_long_counterexample() -> PipelineResult:
    result = _result()
    result.property_result.results.append(
        FunctionCheckResult(
            "apply_discount", "pricing.py", "failed", True, reason="расхождение",
            kwargs={"price": "0.0", "percent": "-0.5"},
            old_repr="raised ValueError('percent must be between 0 and 100')",
            new_repr="0.0",
        )
    )
    return result


def test_terminal_report_keeps_new_value_of_counterexample():
    console = Console(record=True, width=200, force_terminal=False)

    render_terminal_report(_result_with_long_counterexample(), console)

    assert "after=0.0" in console.export_text()


def test_markdown_report_keeps_new_value_of_counterexample():
    text = render_markdown_report(_result_with_long_counterexample(), comment_marker="<!-- m -->")

    assert "after=`0.0`" in text


def test_long_details_are_clipped_with_ellipsis():
    result = _result()
    result.property_result.results.append(
        FunctionCheckResult("noisy", "a.py", "skipped", True, reason="x" * 1000)
    )
    console = Console(record=True, width=400, force_terminal=False)

    render_terminal_report(result, console)
    terminal = console.export_text()
    markdown = render_markdown_report(result, comment_marker="<!-- m -->")

    assert "x" * 1000 not in terminal
    assert "…" in terminal
    assert "x" * 1000 not in markdown
    assert "…" in markdown
