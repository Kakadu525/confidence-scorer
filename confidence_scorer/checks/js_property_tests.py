from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from confidence_scorer.checks.js_strategy import ts_type_to_spec
from confidence_scorer.checks.property_tests import FunctionCheckResult
from confidence_scorer.checks.strategy_builder import Spec
from confidence_scorer.config import Config
from confidence_scorer.extractors.js_extractor import (
    ChangedJsFunction,
    ExtractedJsFunction,
    diff_functions,
    node_available,
)
from confidence_scorer.git_diff import ChangedFile
from confidence_scorer.i18n import current_language, tr

JS_HELPERS_DIR = Path(__file__).resolve().parent.parent / "js_helpers"
DIFF_WORKER_SCRIPT = JS_HELPERS_DIR / "diff_worker.js"


@dataclass
class _FnLike:
    qualname: str
    source: str
    docstring: str | None = None


JsStrategyAI = Callable[[_FnLike, list[str]], "dict[str, Spec] | None"]


def _build_param_specs(fn: ExtractedJsFunction, strategy_ai: JsStrategyAI | None) -> tuple[list[dict] | None, str | None]:
    if any(p.kind == "rest" for p in fn.params):
        return None, tr(
            "rest parameters (...args) are not supported by the input generator",
            "rest-параметры (...args) не поддерживаются генератором входных данных",
        )

    ordered_specs: list[dict] = []
    unresolved: list[str] = []
    for p in fn.params:
        spec = ts_type_to_spec(p.type_annotation)
        ordered_specs.append({"name": p.name, "spec": spec})
        if spec is None:
            unresolved.append(p.name)

    if not unresolved:
        return ordered_specs, None

    if strategy_ai is None:
        return None, tr(
            "no TS type hints and AI strategy generation is disabled", "нет TS type hints и AI-генерация стратегий выключена"
        ) + f": {', '.join(unresolved)}"

    ai_specs = strategy_ai(_FnLike(fn.name, fn.source), unresolved)
    if not ai_specs:
        return None, tr(
            "could not build an input generator for", "не удалось построить генератор входных данных для"
        ) + f": {', '.join(unresolved)}"

    for entry in ordered_specs:
        if entry["spec"] is None:
            if entry["name"] not in ai_specs:
                return None, tr(
                    f"the AI did not propose a strategy for parameter '{entry['name']}'",
                    f"AI не предложил стратегию для параметра '{entry['name']}'",
                )
            entry["spec"] = ai_specs[entry["name"]]

    return ordered_specs, None


def _execute_file_batch(
    file_path: str,
    tasks_for_file: list[dict],
    is_public_by_name: dict[str, bool],
    config: Config,
    deadline: float,
) -> list[FunctionCheckResult]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return [
            FunctionCheckResult(t["name"], file_path, "skipped", True, reason=tr("total time budget exhausted", "исчерпан общий бюджет времени"))
            for t in tasks_for_file
        ]

    timeout_s = min(remaining, float(config.hypothesis.per_file_timeout_s))

    batch = {
        "functions": tasks_for_file,
        "maxExamples": config.js.fast_check_examples,
        "seed": config.js.seed,
        "perFunctionTimeoutS": config.js.per_function_timeout_s,
        "lang": current_language(),
    }

    try:
        proc = subprocess.run(
            [config.js.node_binary, str(DIFF_WORKER_SCRIPT)],
            input=json.dumps(batch),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            cwd=str(JS_HELPERS_DIR),
        )
        payload = json.loads(proc.stdout) if proc.stdout else {"results": []}
    except subprocess.TimeoutExpired:
        return [
            FunctionCheckResult(
                t["name"], file_path, "error", True, reason=tr(f"per-file worker timeout ({timeout_s:.0f}s)", f"таймаут воркера на файл ({timeout_s:.0f}s)")
            )
            for t in tasks_for_file
        ]
    except Exception as exc:  # noqa: BLE001
        return [
            FunctionCheckResult(t["name"], file_path, "error", True, reason=tr(f"worker crashed: {exc!r}", f"воркер упал: {exc!r}"))
            for t in tasks_for_file
        ]

    results = [
        FunctionCheckResult(
            qualname=r["name"],
            file_path=file_path,
            status=r["status"],
            is_public=is_public_by_name.get(r["name"], True),
            reason=r.get("reason"),
            kwargs={"args": r["args"]} if r.get("args") is not None else None,
            old_repr=r.get("oldRepr"),
            new_repr=r.get("newRepr"),
        )
        for r in payload.get("results", [])
    ]

    reported = {r.qualname for r in results}
    for task in tasks_for_file:
        if task["name"] not in reported:
            results.append(
                FunctionCheckResult(
                    task["name"], file_path, "error", True, reason=tr("the worker returned no result for the function", "воркер не вернул результат по функции")
                )
            )
    return results


def run_js_property_tests(
    changed_files: list[ChangedFile],
    config: Config,
    strategy_ai: JsStrategyAI | None = None,
    precomputed_diffs: dict[str, list[ChangedJsFunction]] | None = None,
) -> list[FunctionCheckResult]:
    results: list[FunctionCheckResult] = []

    if not config.execute_changed_code or not config.js.enabled:
        return results

    if not node_available(config.js.node_binary):
        for cf in changed_files:
            if cf.language == "javascript":
                results.append(
                    FunctionCheckResult(
                        "*", cf.path, "skipped", True,
                        reason=tr(
                            "Node.js is not available or js_helpers dependencies are not installed (npm install)",
                            "Node.js недоступен или зависимости js_helpers не установлены (npm install)",
                        ),
                    )
                )
        return results

    deadline = time.monotonic() + config.hypothesis.total_budget_s
    tested_so_far = 0
    file_batches: list[tuple[str, list[dict], dict[str, bool]]] = []

    for cf in changed_files:
        if cf.language != "javascript":
            continue

        if precomputed_diffs is not None:
            changes: list[ChangedJsFunction] = precomputed_diffs.get(cf.path, [])
        else:
            changes = diff_functions(cf.old_content, cf.new_content, config.js.node_binary)
        tasks_for_file = []

        for change in changes:
            if change.change_type != "modified":
                continue
            if tested_so_far >= config.limits.max_functions_per_run:
                results.append(FunctionCheckResult(change.name, cf.path, "skipped", change.new.is_public, reason=tr("max_functions_per_run limit exceeded", "превышен лимит max_functions_per_run")))
                continue

            specs, err = _build_param_specs(change.new, strategy_ai)
            if specs is None:
                results.append(FunctionCheckResult(change.name, cf.path, "skipped", change.new.is_public, reason=err))
                continue

            tasks_for_file.append(
                {
                    "name": change.name,
                    "oldSource": change.old.executable_source,
                    "newSource": change.new.executable_source,
                    "params": specs,
                }
            )
            tested_so_far += 1

        if tasks_for_file:
            is_public_by_name = {c.name: c.new.is_public for c in changes if c.new is not None}
            file_batches.append((cf.path, tasks_for_file, is_public_by_name))

    if not file_batches:
        return results

    max_workers = min(config.limits.max_parallel_workers, len(file_batches))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="confidence-scorer-jspytest") as pool:
        futures = [
            pool.submit(_execute_file_batch, path, tasks, is_public_map, config, deadline)
            for path, tasks, is_public_map in file_batches
        ]
        for future in futures:
            results.extend(future.result())

    return results
