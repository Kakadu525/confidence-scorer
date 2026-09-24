from __future__ import annotations

import copy
import json
import math
import os
import signal
import sys
import threading
import traceback
from typing import Any

from confidence_scorer.checks.strategy_builder import build_explicit_examples, spec_to_strategy
from confidence_scorer.i18n import set_config_language, tr

_HARD_KILL_GRACE_S = 5.0

_RESULTS: list[dict] = []
_PENDING: list[str] = []


class CounterexampleFound(AssertionError):
    def __init__(self, kwargs: dict, old_repr: str, new_repr: str, reason: str):
        super().__init__(reason)
        self.kwargs = kwargs
        self.old_repr = old_repr
        self.new_repr = new_repr
        self.reason = reason


class _Timeout(Exception):
    pass


def _alarm_handler(signum, frame):  # noqa: ARG001
    raise _Timeout()


def _flush_and_exit(reason: str) -> None:
    for qualname in _PENDING:
        _RESULTS.append({"qualname": qualname, "status": "error", "reason": reason})
    try:
        sys.stdout.write(json.dumps({"results": _RESULTS}))
        sys.stdout.flush()
    finally:
        os._exit(0)


class _time_limit:
    def __init__(self, seconds: int):
        self.seconds = max(1, int(seconds))
        self._has_alarm = hasattr(signal, "SIGALRM")
        self._soft_timer: threading.Timer | None = None
        self._hard_timer: threading.Timer | None = None
        self._previous_handler = None

    def _soft_interrupt(self) -> None:
        import _thread

        _thread.interrupt_main()

    def __enter__(self):
        if self._has_alarm:
            self._previous_handler = signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(self.seconds)
        else:
            self._soft_timer = threading.Timer(self.seconds, self._soft_interrupt)
            self._soft_timer.daemon = True
            self._soft_timer.start()

        self._hard_timer = threading.Timer(
            self.seconds + _HARD_KILL_GRACE_S,
            _flush_and_exit,
            args=(tr(f"timeout {self.seconds}s (process force-stopped)", f"таймаут {self.seconds}s (процесс принудительно остановлен)"),),
        )
        self._hard_timer.daemon = True
        self._hard_timer.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._has_alarm:
            signal.alarm(0)
            if self._previous_handler is not None:
                signal.signal(signal.SIGALRM, self._previous_handler)
        if self._soft_timer is not None:
            self._soft_timer.cancel()
        if self._hard_timer is not None:
            self._hard_timer.cancel()
        if exc_type is KeyboardInterrupt and not self._has_alarm:
            raise _Timeout() from exc
        return False


def _outputs_equal(a: Any, b: Any) -> bool:
    if a is b:
        return True

    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b

    if isinstance(a, float) or isinstance(b, float):
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return False
        a_nan, b_nan = isinstance(a, float) and math.isnan(a), isinstance(b, float) and math.isnan(b)
        if a_nan or b_nan:
            return a_nan and b_nan
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)

    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if type(a) is not type(b) or len(a) != len(b):
            return False
        return all(_outputs_equal(x, y) for x, y in zip(a, b, strict=True))

    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        return all(_outputs_equal(a[k], b[k]) for k in a)

    if isinstance(a, (set, frozenset)) and isinstance(b, (set, frozenset)):
        return a == b

    try:
        return bool(a == b)
    except Exception:
        return repr(a) == repr(b)


def _exec_module(source: str, module_name: str) -> dict:
    namespace: dict[str, Any] = {"__name__": module_name}
    exec(compile(source, filename=f"<{module_name}>", mode="exec"), namespace)
    return namespace


def _call(fn, kwargs: dict) -> tuple[Any, BaseException | None]:
    try:
        local_kwargs = copy.deepcopy(kwargs)
    except Exception:
        local_kwargs = kwargs
    try:
        return fn(**local_kwargs), None
    except Exception as exc:
        return None, exc


def _is_self_consistent(fn, kwargs: dict, timeout_s: int, attempts: int = 3) -> bool:
    try:
        with _time_limit(timeout_s):
            first_result, first_exc = _call(fn, kwargs)
            for _ in range(attempts - 1):
                result, exc = _call(fn, kwargs)
                if (first_exc is None) != (exc is None):
                    return False
                if first_exc is None and not _outputs_equal(first_result, result):
                    return False
    except (_Timeout, KeyboardInterrupt):
        return False
    return True


def _run_one_function(
    qualname: str,
    param_specs: dict,
    old_ns: dict,
    new_ns: dict,
    max_examples: int,
    per_function_timeout_s: int,
    seed: int | None,
) -> dict:
    old_fn = old_ns.get(qualname)
    new_fn = new_ns.get(qualname)
    if not callable(old_fn) or not callable(new_fn):
        return {"qualname": qualname, "status": "error", "reason": tr("function not found after exec", "функция не найдена после exec")}

    try:
        strategies = {name: spec_to_strategy(spec) for name, spec in param_specs.items()}
    except Exception as exc:
        return {"qualname": qualname, "status": "skipped", "reason": tr("unsupported generator input", "неподдерживаемый generator input") + f": {exc!r}"}

    from hypothesis import HealthCheck, example, given, settings
    from hypothesis import seed as hypothesis_seed

    def check(**kwargs):
        old_result, old_exc = _call(old_fn, kwargs)
        new_result, new_exc = _call(new_fn, kwargs)

        if (old_exc is None) != (new_exc is None):
            raise CounterexampleFound(
                kwargs,
                f"raised {old_exc!r}" if old_exc else repr(old_result)[:300],
                f"raised {new_exc!r}" if new_exc else repr(new_result)[:300],
                tr(
                    "the old and the new version disagree on whether they raise an exception",
                    "старая и новая версия расходятся в том, бросают ли они исключение",
                ),
            )
        if old_exc is None and not _outputs_equal(old_result, new_result):
            raise CounterexampleFound(
                kwargs,
                repr(old_result)[:300],
                repr(new_result)[:300],
                tr(
                    "the old and the new version return different results for the same input",
                    "старая и новая версия возвращают разные результаты на одном входе",
                ),
            )

    try:
        wrapped = given(**strategies)(check)
        for explicit_kwargs in build_explicit_examples(param_specs):
            wrapped = example(**explicit_kwargs)(wrapped)
        wrapped = settings(
            max_examples=max_examples,
            deadline=None,
            database=None,
            suppress_health_check=list(HealthCheck),
        )(wrapped)
        if seed is not None:
            wrapped = hypothesis_seed(seed)(wrapped)
        with _time_limit(per_function_timeout_s):
            wrapped()
    except CounterexampleFound as ce:
        consistency_budget = max(1, per_function_timeout_s // 2)
        if not _is_self_consistent(old_fn, ce.kwargs, consistency_budget) or not _is_self_consistent(
            new_fn, ce.kwargs, consistency_budget
        ):
            return {
                "qualname": qualname,
                "status": "skipped",
                "reason": tr(
                    "the function is non-deterministic (different results for the same input), "
                    "differential testing does not apply",
                    "функция недетерминирована (разные результаты на одном входе), "
                    "differential testing неприменим",
                ),
            }
        return {
            "qualname": qualname,
            "status": "failed",
            "kwargs": {k: repr(v)[:200] for k, v in ce.kwargs.items()},
            "old_repr": ce.old_repr,
            "new_repr": ce.new_repr,
            "reason": ce.reason,
        }
    except _Timeout:
        return {"qualname": qualname, "status": "error", "reason": tr(f"timeout {per_function_timeout_s}s", f"таймаут {per_function_timeout_s}s")}
    except KeyboardInterrupt:
        return {"qualname": qualname, "status": "error", "reason": tr(f"timeout {per_function_timeout_s}s", f"таймаут {per_function_timeout_s}s")}
    except Exception as exc:
        return {
            "qualname": qualname,
            "status": "error",
            "reason": f"{exc.__class__.__name__}: {exc}",
            "traceback": traceback.format_exc(limit=3),
        }

    return {"qualname": qualname, "status": "passed"}


def run_batch(task_batch: dict) -> list[dict]:
    _RESULTS.clear()
    _PENDING.clear()

    max_examples = task_batch.get("max_examples", 50)
    per_function_timeout_s = task_batch.get("per_function_timeout_s", 10)
    seed = task_batch.get("seed")
    functions = task_batch.get("functions", [])
    _PENDING.extend(f["qualname"] for f in functions)

    try:
        old_ns = _exec_module(task_batch["old_source"], "confidence_scorer_old")
        new_ns = _exec_module(task_batch["new_source"], "confidence_scorer_new")
    except Exception as exc:
        reason = f"exec failed: {exc!r}"
        _RESULTS.extend({"qualname": f["qualname"], "status": "error", "reason": reason} for f in functions)
        _PENDING.clear()
        return list(_RESULTS)

    for func_task in functions:
        qualname = func_task["qualname"]
        result = _run_one_function(
            qualname,
            func_task["param_specs"],
            old_ns,
            new_ns,
            max_examples,
            per_function_timeout_s,
            seed,
        )
        _RESULTS.append(result)
        if qualname in _PENDING:
            _PENDING.remove(qualname)

    return list(_RESULTS)


def main() -> None:
    task_batch = json.loads(sys.stdin.read())
    set_config_language(task_batch.get("lang"))

    repo_dir = task_batch.get("repo_dir")
    if repo_dir and repo_dir not in sys.path:
        sys.path.insert(0, repo_dir)

    results = run_batch(task_batch)
    json.dump({"results": results}, sys.stdout)


if __name__ == "__main__":
    main()
