from __future__ import annotations

import time

import pytest

from confidence_scorer.checks.js_property_tests import run_js_property_tests
from confidence_scorer.config import Config
from confidence_scorer.extractors.js_extractor import diff_files, diff_functions_checked, node_available
from confidence_scorer.git_diff import ChangedFile

pytestmark = pytest.mark.skipif(
    not node_available("node"),
    reason="нужен Node.js и установленные зависимости js_helpers (npm install)",
)

_CLAMP_OLD = (
    "export function clamp(v: number, lo: number, hi: number): number {\n"
    "  if (v < lo) return lo;\n"
    "  if (v > hi) return hi;\n"
    "  return v;\n"
    "}\n"
)


def test_diff_files_handles_every_file_in_one_node_process():
    new = _CLAMP_OLD.replace("return v;", "return v + 0;")
    files = [(f"f{i}.ts", _CLAMP_OLD, new) for i in range(8)]

    started = time.monotonic()
    diffs, issues = diff_files(files, "node")
    elapsed = time.monotonic() - started

    assert issues == {}
    assert len(diffs) == 8
    assert [c.name for c in diffs["f0.ts"]] == ["clamp"]
    assert elapsed < 2.0, f"пакетный разбор занял {elapsed:.2f}s, похоже, процесс снова один на файл"


def test_parse_error_is_reported_not_swallowed():
    broken = "export function broken(a: number { return a;"
    changes, error = diff_functions_checked(_CLAMP_OLD, broken, "node")

    assert changes == []
    assert error and "parse error" in error


def test_diff_files_reports_broken_file_but_keeps_others():
    broken = "export function broken(a: number { return a;"
    good_new = _CLAMP_OLD.replace("return v;", "return v + 0;")

    diffs, issues = diff_files([("ok.ts", _CLAMP_OLD, good_new), ("bad.ts", _CLAMP_OLD, broken)], "node")

    assert "ok.ts" in diffs
    assert "bad.ts" in issues
    assert "bad.ts" not in diffs


def test_executable_source_is_stripped_of_types():
    new = _CLAMP_OLD.replace("return v;", "return v + 0;")
    diffs, _ = diff_files([("a.ts", _CLAMP_OLD, new)], "node")
    change = diffs["a.ts"][0]

    assert ": number" not in change.new.executable_source
    assert ": number" in change.new.source


def test_failing_property_does_not_burn_the_time_budget():
    old = "export function push(xs: number[]): number {\n  xs.push(1);\n  return xs.length;\n}\n"
    new = "export function push(xs: number[]): number {\n  xs.push(1);\n  return xs.length + 0;\n}\n"

    started = time.monotonic()
    results = run_js_property_tests([ChangedFile("p.ts", "M", old, new, "javascript")], Config())
    elapsed = time.monotonic() - started

    assert elapsed < 30, f"прогон занял {elapsed:.0f}s, шринкинг снова не ограничен"
    assert len(results) == 1
    assert results[0].status == "passed", results[0].reason


def test_real_js_regression_is_caught():
    old = "export function rate(base: number, bonus: number): number {\n  return base * 2 + bonus;\n}\n"
    new = "export function rate(base: number, bonus: number): number {\n  return base * 2 - bonus;\n}\n"

    results = run_js_property_tests([ChangedFile("r.ts", "M", old, new, "javascript")], Config())

    assert [r.status for r in results] == ["failed"]
    assert results[0].old_repr != results[0].new_repr


def test_nondeterministic_js_function_is_skipped():
    old = "export function f(n: number): number {\n  return n + Math.floor(Math.random() * 10);\n}\n"
    new = "export function f(n: number): number {\n  return n + Math.floor(Math.random() * 11) - 1;\n}\n"

    results = run_js_property_tests([ChangedFile("n.ts", "M", old, new, "javascript")], Config())

    assert [r.status for r in results] == ["skipped"]
    assert "недетерминирована" in results[0].reason
