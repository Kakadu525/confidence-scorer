from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from confidence_scorer.checks.strategy_builder import Spec, annotation_to_spec
from confidence_scorer.config import Config
from confidence_scorer.extractors.python_extractor import ChangedFunction, ExtractedFunction, diff_functions
from confidence_scorer.git_diff import ChangedFile
from confidence_scorer.i18n import current_language, tr

StrategyAI = Callable[[ExtractedFunction, list[str]], "dict[str, Spec] | None"]


@dataclass
class FunctionCheckResult:
    qualname: str
    file_path: str
    status: str
    is_public: bool
    reason: str | None = None
    kwargs: dict | None = None
    old_repr: str | None = None
    new_repr: str | None = None


@dataclass
class PropertyTestCheckResult:
    results: list[FunctionCheckResult] = field(default_factory=list)

    @property
    def tested(self) -> list[FunctionCheckResult]:
        return [r for r in self.results if r.status in ("passed", "failed")]

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.status == "passed")

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.status == "error")

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")

    @property
    def sub_score(self) -> float | None:
        denom = self.passed_count + self.failed_count
        if denom == 0:
            return None
        return 100.0 * self.passed_count / denom

    @property
    def notes(self) -> list[str]:
        out: list[str] = []
        if self.error_count:
            out.append(
                tr(
                    f"property_tests: {self.error_count} function(s) could not be checked for technical reasons "
                    f"(see the error status in the report); they are left out of the score and not counted as failures",
                    f"property_tests: {self.error_count} функц. не удалось проверить технически "
                    f"(см. статус error в отчёте); они исключены из score и не засчитаны как провал",
                )
            )
        return out

    @property
    def hard_fail_reasons(self) -> list[str]:
        reasons = []
        for r in self.results:
            if r.status == "failed" and r.is_public:
                reasons.append(
                    f"{r.file_path}::{r.qualname}: {r.reason} "
                    + tr(
                        f"(input={r.kwargs}, before={r.old_repr}, after={r.new_repr})",
                        f"(вход={r.kwargs}, было={r.old_repr}, стало={r.new_repr})",
                    )
                )
        return reasons


def _build_param_specs(
    fn: ExtractedFunction, strategy_ai: StrategyAI | None
) -> tuple[dict[str, Spec] | None, str | None]:
    if any(a.kind in ("vararg", "kwarg") for a in fn.args):
        return None, tr(
            "*args/**kwargs are not supported by the input generator",
            "*args/**kwargs не поддерживаются генератором входных данных",
        )

    specs: dict[str, Spec] = {}
    unresolved: list[str] = []
    for arg in fn.args:
        spec = annotation_to_spec(arg.annotation)
        if spec is not None:
            specs[arg.name] = spec
        else:
            unresolved.append(arg.name)

    if not unresolved:
        return specs, None

    if strategy_ai is None:
        return None, tr(
            "no type hints and AI strategy generation is disabled", "нет type hints и AI-генерация стратегий выключена"
        ) + f": {', '.join(unresolved)}"

    ai_specs = strategy_ai(fn, unresolved)
    if not ai_specs:
        return None, tr(
            "could not build an input generator for", "не удалось построить генератор входных данных для"
        ) + f": {', '.join(unresolved)}"

    for name in unresolved:
        if name not in ai_specs:
            return None, tr(
                f"the AI did not propose a strategy for parameter '{name}'",
                f"AI не предложил стратегию для параметра '{name}'",
            )
        specs[name] = ai_specs[name]

    return specs, None


@dataclass
class _FileBatch:
    path: str
    old_source: str
    new_source: str
    tasks: list[dict]
    is_public_by_qualname: dict[str, bool]


def _worker_env(repo_dir: str) -> dict[str, str]:
    own_root = str(Path(__file__).resolve().parent.parent.parent)
    env = dict(os.environ)
    parts = [repo_dir, own_root]
    existing = env.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _execute_file_batch(
    batch: _FileBatch,
    config: Config,
    deadline: float,
    repo_dir: str,
) -> list[FunctionCheckResult]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return [
            FunctionCheckResult(t["qualname"], batch.path, "skipped", True, reason=tr("total time budget exhausted", "исчерпан общий бюджет времени"))
            for t in batch.tasks
        ]

    timeout_s = min(remaining, float(config.hypothesis.per_file_timeout_s))

    payload = {
        "file_path": batch.path,
        "old_source": batch.old_source,
        "new_source": batch.new_source,
        "functions": batch.tasks,
        "max_examples": config.hypothesis.max_examples,
        "lang": current_language(),
        "per_function_timeout_s": config.hypothesis.per_function_timeout_s,
        "seed": config.hypothesis.seed,
        "repo_dir": repo_dir,
    }

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "confidence_scorer.checks._diff_worker"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            cwd=repo_dir,
            env=_worker_env(repo_dir),
        )
        payload_out = json.loads(proc.stdout) if proc.stdout else {"results": []}
    except subprocess.TimeoutExpired:
        return [
            FunctionCheckResult(
                t["qualname"], batch.path, "error", True, reason=tr(f"per-file worker timeout ({timeout_s:.0f}s)", f"таймаут воркера на файл ({timeout_s:.0f}s)")
            )
            for t in batch.tasks
        ]
    except Exception as exc:  # noqa: BLE001
        return [
            FunctionCheckResult(t["qualname"], batch.path, "error", True, reason=tr(f"worker crashed: {exc!r}", f"воркер упал: {exc!r}"))
            for t in batch.tasks
        ]

    results = [
        FunctionCheckResult(
            qualname=r["qualname"],
            file_path=batch.path,
            status=r["status"],
            is_public=batch.is_public_by_qualname.get(r["qualname"], True),
            reason=r.get("reason"),
            kwargs=r.get("kwargs"),
            old_repr=r.get("old_repr"),
            new_repr=r.get("new_repr"),
        )
        for r in payload_out.get("results", [])
    ]

    reported = {r.qualname for r in results}
    for task in batch.tasks:
        if task["qualname"] not in reported:
            results.append(
                FunctionCheckResult(
                    task["qualname"], batch.path, "error", True, reason=tr("the worker returned no result for the function", "воркер не вернул результат по функции")
                )
            )
    return results


def run_property_tests(
    changed_files: list[ChangedFile],
    config: Config,
    strategy_ai: StrategyAI | None = None,
    precomputed_diffs: dict[str, list[ChangedFunction]] | None = None,
    repo_dir: str | Path = ".",
) -> PropertyTestCheckResult:
    check_result = PropertyTestCheckResult()

    if not config.execute_changed_code:
        return check_result

    repo_dir = str(Path(repo_dir).resolve())
    deadline = time.monotonic() + config.hypothesis.total_budget_s
    functions_tested_so_far = 0

    file_batches: list[_FileBatch] = []

    for cf in changed_files:
        if cf.language != "python":
            continue

        if precomputed_diffs is not None:
            changes: list[ChangedFunction] = precomputed_diffs.get(cf.path, [])
        else:
            changes = diff_functions(cf.old_content, cf.new_content)
        tasks_for_file = []

        for change in changes:
            if change.change_type != "modified":
                continue
            if "." in change.qualname:
                continue
            if functions_tested_so_far >= config.limits.max_functions_per_run:
                check_result.results.append(
                    FunctionCheckResult(
                        change.qualname, cf.path, "skipped", change.new.is_public,
                        reason=tr("max_functions_per_run limit exceeded", "превышен лимит max_functions_per_run"),
                    )
                )
                continue

            specs, err = _build_param_specs(change.new, strategy_ai)
            if specs is None:
                check_result.results.append(
                    FunctionCheckResult(change.qualname, cf.path, "skipped", change.new.is_public, reason=err)
                )
                continue

            tasks_for_file.append({"qualname": change.qualname, "param_specs": specs})
            functions_tested_so_far += 1

        if tasks_for_file:
            file_batches.append(
                _FileBatch(
                    path=cf.path,
                    old_source=cf.old_content or "",
                    new_source=cf.new_content or "",
                    tasks=tasks_for_file,
                    is_public_by_qualname={c.qualname: c.new.is_public for c in changes if c.new is not None},
                )
            )

    if not file_batches:
        return check_result

    max_workers = min(config.limits.max_parallel_workers, len(file_batches))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="confidence-scorer-pytest") as pool:
        futures = [pool.submit(_execute_file_batch, batch, config, deadline, repo_dir) for batch in file_batches]
        for future in futures:
            check_result.results.extend(future.result())

    return check_result
