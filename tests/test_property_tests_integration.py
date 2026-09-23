from confidence_scorer.checks.property_tests import run_property_tests
from confidence_scorer.config import Config
from confidence_scorer.git_diff import ChangedFile


def test_catches_boundary_regression():
    old = (
        "def is_prime(n: int) -> bool:\n"
        "    if n < 2:\n        return False\n"
        "    for i in range(2, n):\n        if n % i == 0:\n            return False\n"
        "    return True\n"
    )
    new = old.replace("if n < 2:", "if n <= 2:")

    cf = ChangedFile(path="m.py", status="M", old_content=old, new_content=new, language="python")
    result = run_property_tests([cf], Config())

    assert result.failed_count == 1
    assert result.sub_score == 0.0
    assert result.hard_fail_reasons


def test_equivalent_refactor_passes():
    old = "def double(n: int) -> int:\n    return n + n\n"
    new = "def double(n: int) -> int:\n    return n * 2\n"

    cf = ChangedFile(path="m.py", status="M", old_content=old, new_content=new, language="python")
    result = run_property_tests([cf], Config())

    assert result.failed_count == 0
    assert result.passed_count == 1
    assert result.sub_score == 100.0


def test_skips_functions_without_type_hints_when_ai_disabled():
    old = "def f(x):\n    return x\n"
    new = "def f(x):\n    return x + 1\n"
    cf = ChangedFile(path="m.py", status="M", old_content=old, new_content=new, language="python")
    result = run_property_tests([cf], Config(), strategy_ai=None)
    assert result.skipped_count == 1
    assert result.sub_score is None


def test_execute_changed_code_false_skips_everything():
    old = "def f(n: int) -> int:\n    return n\n"
    new = "def f(n: int) -> int:\n    return n + 1\n"
    cf = ChangedFile(path="m.py", status="M", old_content=old, new_content=new, language="python")
    cfg = Config()
    cfg.execute_changed_code = False
    result = run_property_tests([cf], cfg)
    assert result.results == []
    assert result.sub_score is None


def test_methods_are_not_differential_tested():
    old = "class C:\n    def f(self, n: int) -> int:\n        return n\n"
    new = "class C:\n    def f(self, n: int) -> int:\n        return n + 1\n"
    cf = ChangedFile(path="m.py", status="M", old_content=old, new_content=new, language="python")
    result = run_property_tests([cf], Config())
    assert result.results == []
