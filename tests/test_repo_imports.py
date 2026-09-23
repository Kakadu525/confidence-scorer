from __future__ import annotations

from confidence_scorer.checks.property_tests import run_property_tests
from confidence_scorer.config import Config
from confidence_scorer.git_diff import ChangedFile

_HELPERS = "def bump(n):\n    return n + 1\n"
_CORE_OLD = "from pkg.helpers import bump\n\n\ndef score(n: int) -> int:\n    return bump(n) * 2\n"
_CORE_NEW = "from pkg.helpers import bump\n\n\ndef score(n: int) -> int:\n    return bump(n) * 3\n"


def _make_repo(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "helpers.py").write_text(_HELPERS, encoding="utf-8")
    (pkg / "core.py").write_text(_CORE_NEW, encoding="utf-8")
    return tmp_path


def test_intra_repo_import_resolves_and_regression_is_found(tmp_path):
    repo = _make_repo(tmp_path)
    changed = ChangedFile("pkg/core.py", "M", _CORE_OLD, _CORE_NEW, "python")

    result = run_property_tests([changed], Config(), repo_dir=str(repo))

    assert result.error_count == 0, [r.reason for r in result.results]
    assert result.failed_count == 1
    assert result.hard_fail_reasons


def test_equivalent_change_in_importing_file_passes(tmp_path):
    repo = _make_repo(tmp_path)
    new = _CORE_OLD.replace("bump(n) * 2", "bump(n) + bump(n)")
    (repo / "pkg" / "core.py").write_text(new, encoding="utf-8")
    changed = ChangedFile("pkg/core.py", "M", _CORE_OLD, new, "python")

    result = run_property_tests([changed], Config(), repo_dir=str(repo))

    assert result.error_count == 0, [r.reason for r in result.results]
    assert result.passed_count == 1
    assert result.sub_score == 100.0
