from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from confidence_scorer.ai.prompts import second_reviewer_prompt, semantic_diff_prompt
from confidence_scorer.checks.property_tests import FunctionCheckResult, PropertyTestCheckResult
from confidence_scorer.checks.second_reviewer import SecondReviewResult
from confidence_scorer.checks.semantic_diff import SemanticDiffCheckResult
from confidence_scorer.cli import main
from confidence_scorer.config import Config
from confidence_scorer.git_diff import ChangedFile
from confidence_scorer.i18n import ENV_VAR, current_language, set_config_language, tr
from confidence_scorer.pipeline import PipelineResult
from confidence_scorer.report.markdown import render_markdown_report
from confidence_scorer.scoring import compute_score


def _markdown() -> str:
    config = Config()
    pt = PropertyTestCheckResult(results=[FunctionCheckResult("f", "a.py", "passed", True)])
    sd = SemanticDiffCheckResult(results=[])
    rv = SecondReviewResult(confidence=90, summary="ok")
    score = compute_score(pt, sd, rv, config)
    result = PipelineResult(
        config=config,
        changed_files=[ChangedFile("a.py", "M", "old", "new", "python")],
        function_changes=[],
        property_result=pt,
        semantic_result=sd,
        review_result=rv,
        score=score,
    )
    return render_markdown_report(result)


def test_english_is_the_default():
    assert current_language() == "en"
    assert tr("Notes", "Примечания") == "Notes"


def test_config_language_switches_output():
    set_config_language("ru")
    assert tr("Notes", "Примечания") == "Примечания"


def test_env_var_beats_config(monkeypatch):
    set_config_language("ru")
    monkeypatch.setenv(ENV_VAR, "en")
    assert current_language() == "en"


@pytest.mark.parametrize("value", ["de", "", "  RU  "])
def test_unknown_values_fall_back_or_normalize(monkeypatch, value):
    monkeypatch.setenv(ENV_VAR, value)
    assert current_language() == ("ru" if value.strip().lower() == "ru" else "en")


def test_config_rejects_unsupported_language():
    assert Config(language="ru").language == "ru"
    with pytest.raises(ValidationError):
        Config(language="de")


def test_markdown_report_follows_language():
    english = _markdown()
    assert "| Check | Sub-score | Weight |" in english
    assert "Trust, but verify" in english

    set_config_language("ru")
    russian = _markdown()
    assert "| Проверка | Sub-score | Вес |" in russian
    assert "Доверяйте, но проверяйте" in russian


def test_prompts_ask_for_answers_in_the_output_language():
    system, _ = second_reviewer_prompt("diff", "- a.py (modified)")
    assert "in English" in system

    set_config_language("ru")
    system, _ = semantic_diff_prompt("a.py", "f", "old", "new")
    assert "in Russian" in system
    # Severity values are parsed by code and must never be translated.
    assert '"low"|"medium"|"high"' in system


def test_cli_lang_flag_switches_doctor_output(tmp_path):
    result = CliRunner().invoke(main, ["--lang", "ru", "doctor", "--repo", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Готовность проверок" in result.output


def test_config_language_switches_doctor_output(tmp_path):
    (tmp_path / "confidence.yml").write_text("language: ru\n", encoding="utf-8")
    result = CliRunner().invoke(main, ["doctor", "--repo", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Готовность проверок" in result.output


@pytest.mark.parametrize(("args", "language", "marker"), [([], "en", "Keys are never written"), (["--lang", "ru"], "ru", "Ключи в этот файл")])
def test_init_writes_a_valid_template_in_the_chosen_language(tmp_path, args, language, marker):
    target = tmp_path / "confidence.yml"
    result = CliRunner().invoke(main, [*args, "init", "--path", str(target)])
    assert result.exit_code == 0, result.output

    text = target.read_text(encoding="utf-8")
    assert marker in text
    config = Config(**yaml.safe_load(text))
    assert config.language == language


def test_python_worker_uses_the_language_from_the_batch():
    root = Path(__file__).resolve().parent.parent
    payload = {
        "file_path": "m.py",
        "old_source": "import random\ndef f(n: int) -> int:\n    return n + random.randint(0, 10)\n",
        "new_source": "import random\ndef f(n: int) -> int:\n    return n + random.randint(0, 11) - 1\n",
        "functions": [{"qualname": "f", "param_specs": {"n": {"kind": "integers"}}}],
        "max_examples": 30,
        "per_function_timeout_s": 5,
        "seed": 0,
        "lang": "ru",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "confidence_scorer.checks._diff_worker"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=root,
        timeout=60,
        check=True,
    )
    reason = json.loads(proc.stdout)["results"][0]["reason"]
    assert "недетерминирована" in reason
