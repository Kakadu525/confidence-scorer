from __future__ import annotations

import time

import pytest

from confidence_scorer.checks._diff_worker import _outputs_equal, run_batch


def _batch(old: str, new: str, functions: list[dict], **kwargs) -> dict:
    payload = {
        "file_path": "m.py",
        "old_source": old,
        "new_source": new,
        "functions": functions,
        "max_examples": 30,
        "per_function_timeout_s": 5,
        "seed": 0,
    }
    payload.update(kwargs)
    return payload


def _one(old: str, new: str, specs: dict, qualname: str = "f", **kwargs) -> dict:
    results = run_batch(_batch(old, new, [{"qualname": qualname, "param_specs": specs}], **kwargs))
    assert len(results) == 1
    return results[0]


class TestOutputsEqual:
    def test_nan_equals_itself(self):
        assert _outputs_equal(float("nan"), float("nan"))

    def test_nan_differs_from_number(self):
        assert not _outputs_equal(float("nan"), 1.0)

    def test_float_tolerance(self):
        assert _outputs_equal(0.1 + 0.2, 0.3)

    def test_floats_inside_containers_use_tolerance(self):
        assert _outputs_equal([0.1 + 0.2], [0.3])
        assert _outputs_equal({"x": 0.1 + 0.2}, {"x": 0.3})
        assert _outputs_equal((1, [float("nan")]), (1, [float("nan")]))

    def test_distinct_values_still_differ(self):
        assert not _outputs_equal([1, 2], [1, 3])
        assert not _outputs_equal({"a": 1}, {"b": 1})

    def test_bool_is_not_int(self):
        assert not _outputs_equal(True, 1)
        assert _outputs_equal(True, True)

    def test_infinities(self):
        assert _outputs_equal(float("inf"), float("inf"))
        assert not _outputs_equal(float("inf"), float("-inf"))


class TestFalsePositives:
    def test_nan_returning_function_passes(self):
        old = "def f(x: float) -> float:\n    return x * float('nan')\n"
        new = "def f(x: float) -> float:\n    return float('nan') * x\n"
        assert _one(old, new, {"x": {"kind": "floats"}})["status"] == "passed"

    def test_argument_mutation_does_not_create_counterexample(self):
        old = "def f(xs: list[int]) -> int:\n    xs.append(1)\n    return len(xs)\n"
        new = "def f(xs: list[int]) -> int:\n    xs.append(1)\n    return len(xs) + 0\n"
        assert _one(old, new, {"xs": {"kind": "lists", "elements": {"kind": "integers"}}})["status"] == "passed"

    def test_dict_mutation_does_not_create_counterexample(self):
        old = "def f(d: dict[str, int]) -> int:\n    d['seen'] = 1\n    return len(d)\n"
        new = "def f(d: dict[str, int]) -> int:\n    d['seen'] = 1\n    return len(d) * 1\n"
        spec = {"kind": "dictionaries", "keys": {"kind": "text"}, "values": {"kind": "integers"}}
        assert _one(old, new, {"d": spec})["status"] == "passed"

    def test_nondeterministic_function_is_skipped_not_failed(self):
        old = "import random\ndef f(n: int) -> int:\n    return n + random.randint(0, 10)\n"
        new = "import random\ndef f(n: int) -> int:\n    return n + random.randint(0, 11) - 1\n"
        result = _one(old, new, {"n": {"kind": "integers"}})
        assert result["status"] == "skipped"
        assert "non-deterministic" in result["reason"]


class TestDetection:
    def test_real_regression_is_caught(self):
        old = "def f(a: int, b: int) -> int:\n    return a * 2 + b\n"
        new = "def f(a: int, b: int) -> int:\n    return a * 2 - b\n"
        result = _one(old, new, {"a": {"kind": "integers"}, "b": {"kind": "integers"}})
        assert result["status"] == "failed"
        assert result["old_repr"] != result["new_repr"]

    def test_exception_divergence_is_caught(self):
        old = "def f(n: int) -> int:\n    return n\n"
        new = "def f(n: int) -> int:\n    if n == 0:\n        raise ValueError('нет')\n    return n\n"
        result = _one(old, new, {"n": {"kind": "integers"}})
        assert result["status"] == "failed"
        assert "exception" in result["reason"]

    def test_string_regression_is_caught_deterministically(self):
        old = "def f(s: str) -> str:\n    return s.strip().lower()\n"
        new = "def f(s: str) -> str:\n    return s.strip().lower().replace(' ', '-')\n"
        statuses = {_one(old, new, {"s": {"kind": "text"}})["status"] for _ in range(3)}
        assert statuses == {"failed"}

    def test_seed_makes_runs_reproducible(self):
        old = "def f(s: str) -> str:\n    return s.upper()\n"
        new = "def f(s: str) -> str:\n    return s.upper() if len(s) < 3 else s\n"
        runs = [_one(old, new, {"s": {"kind": "text"}}, seed=1234) for _ in range(3)]
        assert len({r["status"] for r in runs}) == 1


class TestResilience:
    def test_per_function_timeout_is_enforced_on_every_platform(self):
        old = "def f(n: int) -> int:\n    return n\n"
        new = "def f(n: int) -> int:\n    while True:\n        pass\n"

        started = time.monotonic()
        result = _one(old, new, {"n": {"kind": "integers"}}, per_function_timeout_s=2)
        elapsed = time.monotonic() - started

        assert result["status"] == "error"
        assert "timeout" in result["reason"]
        assert elapsed < 30, f"таймаут не сработал: прогон занял {elapsed:.0f}s"

    def test_unimportable_module_is_error_for_every_function(self):
        old = "import definitely_missing_module_xyz\ndef f(n: int) -> int:\n    return n\n"
        new = "import definitely_missing_module_xyz\ndef f(n: int) -> int:\n    return n + 1\n"
        result = _one(old, new, {"n": {"kind": "integers"}})
        assert result["status"] == "error"
        assert "exec failed" in result["reason"]

    def test_module_is_executed_once_per_version_not_once_per_function(self, tmp_path):
        marker = tmp_path / "execs.txt"
        marker.write_text("", encoding="utf-8")
        side_effect = (
            "from pathlib import Path\n"
            f"Path(r'{marker}').open('a', encoding='utf-8').write('x')\n"
        )
        n_funcs = 4
        old = side_effect + "\n".join(f"def f{i}(n: int) -> int:\n    return n + {i}\n" for i in range(n_funcs))
        new = side_effect + "\n".join(f"def f{i}(n: int) -> int:\n    return n + {i} + 0\n" for i in range(n_funcs))

        results = run_batch(
            _batch(old, new, [{"qualname": f"f{i}", "param_specs": {"n": {"kind": "integers"}}} for i in range(n_funcs)])
        )

        assert [r["status"] for r in results] == ["passed"] * n_funcs
        assert len(marker.read_text(encoding="utf-8")) == 2, "модуль должен исполняться по разу на версию"

    def test_unknown_strategy_kind_is_skipped_not_executed(self):
        old = "def f(x: int) -> int:\n    return x\n"
        new = "def f(x: int) -> int:\n    return x + 1\n"
        result = _one(old, new, {"x": {"kind": "os.system"}})
        assert result["status"] == "skipped"
        assert "generator input" in result["reason"]

    def test_missing_function_reports_error(self):
        old = "def other(n: int) -> int:\n    return n\n"
        new = "def other(n: int) -> int:\n    return n + 1\n"
        result = _one(old, new, {"n": {"kind": "integers"}}, qualname="f")
        assert result["status"] == "error"


@pytest.mark.parametrize("seed", [None, 0, 7])
def test_batch_runs_with_and_without_seed(seed):
    old = "def f(n: int) -> int:\n    return n\n"
    new = "def f(n: int) -> int:\n    return n\n"
    results = run_batch(_batch(old, new, [{"qualname": "f", "param_specs": {"n": {"kind": "integers"}}}], seed=seed))
    assert results[0]["status"] == "passed"
