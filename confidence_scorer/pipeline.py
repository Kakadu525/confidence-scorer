from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from confidence_scorer.ai import get_provider
from confidence_scorer.ai.cache import ResponseCache
from confidence_scorer.ai.strategy_gen import make_strategy_ai
from confidence_scorer.checks.js_property_tests import run_js_property_tests
from confidence_scorer.checks.property_tests import FunctionCheckResult, PropertyTestCheckResult, run_property_tests
from confidence_scorer.checks.review_panel import ReviewPanelResult, run_review_panel
from confidence_scorer.checks.second_reviewer import SecondReviewResult
from confidence_scorer.checks.semantic_diff import SemanticDiffCheckResult, run_semantic_diff
from confidence_scorer.config import Config, load_config
from confidence_scorer.extractors import js_extractor, python_extractor
from confidence_scorer.git_diff import ChangedFile, get_changed_files, unified_diff_text
from confidence_scorer.models import FunctionChange
from confidence_scorer.scoring import ConfidenceScore, compute_score


@dataclass
class FileIssue:
    path: str
    language: str
    reason: str


@dataclass
class PipelineResult:
    config: Config
    changed_files: list[ChangedFile]
    function_changes: list[FunctionChange]
    property_result: PropertyTestCheckResult
    semantic_result: SemanticDiffCheckResult
    review_result: ReviewPanelResult | SecondReviewResult
    score: ConfidenceScore
    file_issues: list[FileIssue] = field(default_factory=list)


def _diff_all_functions(
    changed_files: list[ChangedFile], node_binary: str
) -> tuple[dict[str, list], dict[str, list], list[FileIssue]]:
    python_diffs: dict[str, list] = {}
    js_diffs: dict[str, list] = {}
    issues: list[FileIssue] = []

    js_files: list[tuple[str, str | None, str | None]] = []

    for cf in changed_files:
        if cf.language == "python":
            changes, parse_error = python_extractor.diff_functions_checked(cf.old_content, cf.new_content)
            python_diffs[cf.path] = changes
            if parse_error:
                issues.append(FileIssue(cf.path, "python", parse_error))
        elif cf.language == "javascript":
            js_files.append((cf.path, cf.old_content, cf.new_content))

    if js_files:
        if js_extractor.node_available(node_binary):
            js_diffs, js_issues = js_extractor.diff_files(js_files, node_binary)
            issues.extend(FileIssue(path, "javascript", reason) for path, reason in js_issues.items())
        else:
            issues.extend(
                FileIssue(
                    path,
                    "javascript",
                    "Node.js недоступен или зависимости js_helpers не установлены (npm install)",
                )
                for path, _, _ in js_files
            )

    return python_diffs, js_diffs, issues


def build_function_changes(
    changed_files: list[ChangedFile],
    python_diffs: dict[str, list],
    js_diffs: dict[str, list],
) -> list[FunctionChange]:
    out: list[FunctionChange] = []
    for cf in changed_files:
        if cf.language == "python":
            for c in python_diffs.get(cf.path, []):
                fn = c.new or c.old
                out.append(
                    FunctionChange(
                        cf.path, c.qualname, "python", c.change_type,
                        c.old.source if c.old else None,
                        c.new.source if c.new else None,
                        fn.is_public,
                    )
                )
        elif cf.language == "javascript":
            for c in js_diffs.get(cf.path, []):
                fn = c.new or c.old
                out.append(
                    FunctionChange(
                        cf.path, c.name, "javascript", c.change_type,
                        c.old.source if c.old else None,
                        c.new.source if c.new else None,
                        fn.is_public,
                    )
                )
    return out


def run_pipeline(
    base: str,
    head: str,
    repo_dir: str = ".",
    config_path: str | None = None,
) -> PipelineResult:
    config = load_config(config_path, repo_root=repo_dir)

    changed_files = get_changed_files(
        base, head, repo_dir=repo_dir, exclude_globs=config.exclude, languages=config.languages
    )

    python_diffs, js_diffs, file_issues = _diff_all_functions(changed_files, config.js.node_binary)
    function_changes = build_function_changes(changed_files, python_diffs, js_diffs)

    cache = ResponseCache(
        Path(repo_dir) / config.cache.dir, ttl_s=config.cache.ttl_s, enabled=config.cache.enabled
    )
    strategy_provider = get_provider(config.providers.strategy_generation, cache)
    strategy_ai = make_strategy_ai(strategy_provider)
    semantic_provider = get_provider(config.providers.semantic_diff, cache)
    review_members = [(member, get_provider(member, cache)) for member in config.providers.second_reviewer]

    diff_text = (
        unified_diff_text(base, head, repo_dir, paths=[cf.path for cf in changed_files]) if changed_files else ""
    )

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="confidence-scorer-check") as pool:
        f_property_py = pool.submit(
            run_property_tests, changed_files, config, strategy_ai, python_diffs, repo_dir
        )
        f_property_js = pool.submit(run_js_property_tests, changed_files, config, strategy_ai, js_diffs)
        f_semantic = pool.submit(run_semantic_diff, function_changes, semantic_provider, config)
        f_review = pool.submit(run_review_panel, diff_text, changed_files, review_members, config)

        property_result = f_property_py.result()
        js_results: list[FunctionCheckResult] = f_property_js.result()
        property_result.results.extend(js_results)

        semantic_result = f_semantic.result()
        review_result = f_review.result()

    extra_notes = [f"{issue.path}: файл не проанализирован ({issue.reason})" for issue in file_issues]
    score = compute_score(property_result, semantic_result, review_result, config, extra_notes=extra_notes)

    return PipelineResult(
        config=config,
        changed_files=changed_files,
        function_changes=function_changes,
        property_result=property_result,
        semantic_result=semantic_result,
        review_result=review_result,
        score=score,
        file_issues=file_issues,
    )
