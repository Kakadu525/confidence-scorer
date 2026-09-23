from __future__ import annotations

import pytest
from click.testing import CliRunner

from confidence_scorer import doctor
from confidence_scorer.cli import main
from confidence_scorer.config import Config
from confidence_scorer.doctor import diagnose


@pytest.fixture(autouse=True)
def _sdks_installed(monkeypatch):
    monkeypatch.setattr(doctor, "sdk_installed", lambda _name: True)


def _by_key(diagnosis):
    return {c.key: c for c in diagnosis.checks}


def test_no_keys_leaves_only_property_tests():
    diagnosis = diagnose(Config(), env={}, node_ok=True)
    checks = _by_key(diagnosis)

    assert checks["property_tests"].ready
    assert not checks["semantic_diff"].ready
    assert "ANTHROPIC_API_KEY" in checks["semantic_diff"].detail
    assert "OPENAI_API_KEY" in checks["second_reviewer"].detail
    assert diagnosis.coverage == pytest.approx(0.40)
    assert diagnosis.score_cap == pytest.approx(70.0)


def test_anthropic_only_with_default_config():
    diagnosis = diagnose(Config(), env={"ANTHROPIC_API_KEY": "x"}, node_ok=True)

    assert _by_key(diagnosis)["semantic_diff"].ready
    assert not _by_key(diagnosis)["second_reviewer"].ready
    assert diagnosis.coverage == pytest.approx(0.65)


def test_anthropic_only_after_switching_reviewer_gets_full_coverage():
    config = Config(providers={"second_reviewer": {"provider": "anthropic", "model": "claude-opus-5"}})
    diagnosis = diagnose(config, env={"ANTHROPIC_API_KEY": "x"}, node_ok=True)

    assert all(c.ready for c in diagnosis.checks)
    assert diagnosis.coverage == pytest.approx(1.0)
    assert diagnosis.score_cap is None


def test_key_without_sdk_is_not_ready(monkeypatch):
    monkeypatch.setattr(doctor, "sdk_installed", lambda name: name != "anthropic")
    diagnosis = diagnose(Config(), env={"ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "y"}, node_ok=True)
    semantic = _by_key(diagnosis)["semantic_diff"]

    assert not semantic.ready
    assert "confidence-scorer[anthropic]" in semantic.detail


def test_disabled_code_execution_is_reported():
    config = Config()
    config.execute_changed_code = False
    checks = _by_key(diagnose(config, env={}, node_ok=True))

    assert not checks["property_tests"].ready
    assert "execute_changed_code" in checks["property_tests"].detail


def test_missing_node_is_explained_but_does_not_change_coverage():
    with_node = diagnose(Config(), env={}, node_ok=True)
    without_node = diagnose(Config(), env={}, node_ok=False)

    assert "npm install" in _by_key(without_node)["property_tests_js"].detail
    assert with_node.coverage == without_node.coverage


def test_effort_is_shown_in_details():
    config = Config(providers={"semantic_diff": {"provider": "anthropic", "model": "claude-opus-5", "effort": "low"}})
    detail = _by_key(diagnose(config, env={}, node_ok=True))["semantic_diff"].detail
    assert "effort: low" in detail


def test_cli_doctor_runs_without_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = CliRunner().invoke(main, ["doctor", "--repo", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "ANTHROPIC_API_KEY" in result.output
    assert "не выше 70" in result.output


def test_cli_doctor_reports_invalid_config(tmp_path):
    (tmp_path / "confidence.yml").write_text(
        "providers:\n  semantic_diff:\n    provider: openai\n    model: claude-opus-5\n", encoding="utf-8"
    )
    result = CliRunner().invoke(main, ["doctor", "--repo", str(tmp_path)])

    assert result.exit_code == 2
    assert "модель Claude" in result.output
