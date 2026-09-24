from __future__ import annotations

from rich.console import Console

from confidence_scorer.checks.property_tests import FunctionCheckResult, PropertyTestCheckResult
from confidence_scorer.checks.review_panel import PanelMember, ReviewPanelResult
from confidence_scorer.checks.second_reviewer import SecondReviewResult
from confidence_scorer.checks.semantic_diff import SemanticDiffCheckResult
from confidence_scorer.config import Config
from confidence_scorer.pipeline import PipelineResult
from confidence_scorer.report.json_report import render_json_report
from confidence_scorer.report.markdown import render_markdown_report
from confidence_scorer.report.panel_format import member_score_text, member_status_text
from confidence_scorer.report.terminal import render_terminal_report
from confidence_scorer.scoring import compute_score


def _panel() -> ReviewPanelResult:
    return ReviewPanelResult(
        [
            PanelMember(
                "anthropic/claude-opus-5", 2,
                SecondReviewResult(confidence=95, verdict="ок", summary="всё в порядке"),
                provider="anthropic", model="claude-opus-5",
            ),
            PanelMember(
                "ollama/qwen2.5-coder:7b", 1,
                SecondReviewResult(confidence=90, issues=[{"severity": "medium", "file": "a.py", "description": "спорно"}]),
                provider="ollama", model="qwen2.5-coder:7b",
            ),
            PanelMember(
                "deepseek/deepseek-flash", 1,
                SecondReviewResult(error="не задана переменная окружения DEEPSEEK_API_KEY"),
                provider="deepseek", model="deepseek-flash",
            ),
        ]
    )


def _pipeline_result(review) -> PipelineResult:
    config = Config()
    pt = PropertyTestCheckResult(results=[FunctionCheckResult("f", "a.py", "passed", True)])
    sd = SemanticDiffCheckResult()
    return PipelineResult(
        config=config,
        changed_files=[],
        function_changes=[],
        property_result=pt,
        semantic_result=sd,
        review_result=review,
        score=compute_score(pt, sd, review, config),
    )


def test_member_score_and_status_text():
    opus, qwen, deepseek = _panel().members
    assert member_score_text(opus) == "95"
    assert member_score_text(qwen) == "90 → 75 (cap: medium)"
    assert member_score_text(deepseek) == "-"
    assert member_status_text(opus) == "✓"
    assert member_status_text(deepseek) == "не задана переменная окружения DEEPSEEK_API_KEY"


def test_terminal_shows_panel_table_and_reviewer_column():
    console = Console(record=True, width=200)
    render_terminal_report(_pipeline_result(_panel()), console)
    text = console.export_text()

    assert "Second AI reviewer: panel" in text
    assert "90 → 75 (cap: medium)" in text
    assert "Panel: 88, 75% answered by weight" in text
    assert "Found by" in text


def test_markdown_shows_panel_table_and_reviewer_column():
    md = render_markdown_report(_pipeline_result(_panel()))

    assert "| Reviewer | Weight | Score | Status |" in md
    assert "| anthropic/claude-opus-5 | 2 | 95 | ✓ |" in md
    assert "Panel: 88, 75% answered by weight" in md
    assert "| Severity | File | Description | Found by |" in md


def test_json_has_members_and_answered_share():
    payload = render_json_report(_pipeline_result(_panel()))["second_reviewer"]

    assert payload["confidence"] == 88
    assert payload["answered_share"] == 0.75
    assert payload["verdict"] == "ок"
    assert [m["label"] for m in payload["members"]] == [
        "anthropic/claude-opus-5",
        "ollama/qwen2.5-coder:7b",
        "deepseek/deepseek-flash",
    ]
    qwen = payload["members"][1]
    assert (qwen["confidence"], qwen["score"], qwen["weight"]) == (90, 75.0, 1)
    assert payload["issues"][0]["reviewer"] == "ollama/qwen2.5-coder:7b"


def test_single_reviewer_reports_look_as_before():
    single = SecondReviewResult(confidence=80, issues=[{"severity": "medium", "file": "a.py", "description": "x"}])
    result = _pipeline_result(single)

    console = Console(record=True, width=200)
    render_terminal_report(result, console)
    assert "panel" not in console.export_text()
    md = render_markdown_report(result)
    assert "| Reviewer | Weight |" not in md
    assert "| Severity | File | Description |" in md

    payload = render_json_report(result)["second_reviewer"]
    assert payload["confidence"] == 80
    assert payload["answered_share"] == 1.0
    assert len(payload["members"]) == 1
