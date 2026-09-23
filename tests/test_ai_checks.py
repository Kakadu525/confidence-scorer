from __future__ import annotations

from confidence_scorer.ai.prompts import second_reviewer_prompt, semantic_diff_prompt, strategy_generation_prompt
from confidence_scorer.ai.strategy_gen import make_strategy_ai
from confidence_scorer.checks.second_reviewer import run_second_review
from confidence_scorer.checks.semantic_diff import run_semantic_diff
from confidence_scorer.config import Config
from confidence_scorer.extractors.python_extractor import extract_functions
from confidence_scorer.git_diff import ChangedFile
from confidence_scorer.models import FunctionChange


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, response, available: bool = True):
        self.response = response
        self._available = available
        self.prompts: list[tuple[str, str]] = []

    @property
    def available(self) -> bool:
        return self._available

    def complete_json(self, system: str, user: str, *, max_tokens: int = 1500):
        self.prompts.append((system, user))
        return self.response


def _change(language: str = "python") -> FunctionChange:
    return FunctionChange("a.py", "f", language, "modified", "def f(): return 1", "def f(): return 2", True)


class TestSemanticDiff:
    def test_valid_response_is_parsed(self):
        provider = FakeProvider({"risk_score": 70, "changes": [{"description": "граница", "severity": "high"}]})
        result = run_semantic_diff([_change()], provider, Config())

        assert result.results[0].risk_score == 70
        assert result.sub_score == 40.0
        assert result.notable_changes[0][1]["severity"] == "high"

    def test_risk_score_is_clamped(self):
        result = run_semantic_diff([_change()], FakeProvider({"risk_score": 4200}), Config())
        assert result.sub_score == 100.0

        result = run_semantic_diff([_change()], FakeProvider({"risk_score": -50}), Config())
        assert result.sub_score == 0.0

    def test_garbage_response_does_not_crash_the_check(self):
        for garbage in (None, [], {"нет нужного поля": 1}, "просто текст"):
            result = run_semantic_diff([_change()], FakeProvider(garbage), Config())
            assert result.sub_score is None
            assert result.results[0].error

    def test_non_numeric_risk_score_is_rejected(self):
        result = run_semantic_diff([_change()], FakeProvider({"risk_score": "высокий"}), Config())
        assert result.sub_score is None
        assert "не число" in result.results[0].error

    def test_malformed_changes_are_filtered_out(self):
        provider = FakeProvider({"risk_score": 90, "changes": ["строка", {"без описания": 1}, {"description": "ок"}]})
        result = run_semantic_diff([_change()], provider, Config())
        assert [c["description"] for c in result.results[0].changes] == ["ок"]

    def test_unavailable_provider_is_not_an_error_state(self):
        result = run_semantic_diff([_change()], FakeProvider({"risk_score": 100}, available=False), Config())
        assert result.sub_score is None

    def test_prompt_uses_the_right_language_fence(self):
        provider = FakeProvider({"risk_score": 100})
        run_semantic_diff([_change("javascript")], provider, Config())

        _, user = provider.prompts[0]
        assert "```javascript" in user
        assert "```python" not in user

    def test_respects_max_functions_limit(self):
        config = Config()
        config.limits.max_functions_per_run = 2
        provider = FakeProvider({"risk_score": 100})

        run_semantic_diff([_change() for _ in range(5)], provider, config)

        assert len(provider.prompts) == 2

    def test_added_functions_are_not_sent(self):
        added = FunctionChange("a.py", "f", "python", "added", None, "def f(): ...", True)
        provider = FakeProvider({"risk_score": 100})

        result = run_semantic_diff([added], provider, Config())

        assert provider.prompts == []
        assert result.sub_score is None


class TestSecondReviewer:
    def _files(self):
        return [ChangedFile("a.py", "M", "old", "new", "python")]

    def test_valid_response_is_parsed(self):
        provider = FakeProvider(
            {
                "confidence": 77,
                "verdict": "ок",
                "summary": "всё хорошо",
                "issues": [{"severity": "high", "file": "a.py", "description": "проблема"}],
            }
        )
        result = run_second_review("diff", self._files(), provider, Config())

        assert result.confidence == 77
        assert result.sub_score == 40.0
        assert result.verdict == "ок"
        assert len(result.high_severity_issues) == 1

    def test_confidence_is_clamped_and_validated(self):
        assert run_second_review("d", self._files(), FakeProvider({"confidence": 500}), Config()).confidence == 100
        bad = run_second_review("d", self._files(), FakeProvider({"confidence": "много"}), Config())
        assert bad.confidence is None
        assert "не число" in bad.error

    def test_garbage_response_degrades_to_error(self):
        result = run_second_review("d", self._files(), FakeProvider(None), Config())
        assert result.sub_score is None
        assert result.error

    def test_non_string_verdict_and_summary_are_dropped(self):
        provider = FakeProvider({"confidence": 50, "verdict": 1, "summary": ["a"], "issues": "нет"})
        result = run_second_review("d", self._files(), provider, Config())

        assert result.verdict is None
        assert result.summary is None
        assert result.issues == []

    def test_oversized_diff_is_truncated_before_sending(self):
        config = Config()
        config.limits.max_full_diff_bytes = 100
        provider = FakeProvider({"confidence": 50})

        run_second_review("x" * 10_000, self._files(), provider, config)

        _, user = provider.prompts[0]
        assert "diff обрезан по лимиту" in user
        assert len(user) < 1000

    def test_unavailable_provider_reports_reason(self):
        result = run_second_review("d", self._files(), FakeProvider({}, available=False), Config())
        assert "недоступен" in result.error


class TestStrategyGeneration:
    def _fn(self):
        return extract_functions("def f(items, count):\n    return items[:count]\n")["f"]

    def test_valid_specs_are_accepted(self):
        provider = FakeProvider({"items": {"kind": "lists", "elements": {"kind": "integers"}}, "count": {"kind": "integers"}})
        ask = make_strategy_ai(provider)

        specs = ask(self._fn(), ["items", "count"])

        assert set(specs) == {"items", "count"}

    def test_kind_outside_allowlist_is_dropped(self):
        provider = FakeProvider({"items": {"kind": "os.system"}, "count": {"kind": "integers"}})
        ask = make_strategy_ai(provider)

        specs = ask(self._fn(), ["items", "count"])

        assert set(specs) == {"count"}

    def test_garbage_response_yields_none(self):
        for garbage in (None, [], "текст", {}):
            assert make_strategy_ai(FakeProvider(garbage))(self._fn(), ["items"]) is None

    def test_unavailable_provider_disables_generation(self):
        assert make_strategy_ai(FakeProvider({}, available=False)) is None
        assert make_strategy_ai(None) is None


class TestPrompts:
    def test_strategy_prompt_lists_the_schema_and_params(self):
        fn = extract_functions("def f(x):\n    '''Док.'''\n    return x\n")["f"]
        system, user = strategy_generation_prompt(fn, ["x"])

        assert '"kind": "integers"' in system
        assert "Док." in user
        assert "'x'" in user or "x" in user

    def test_semantic_prompt_demands_json_only(self):
        system, _ = semantic_diff_prompt("a.py", "f", "old", "new")
        assert "risk_score" in system

    def test_second_reviewer_prompt_includes_files_and_diff(self):
        _, user = second_reviewer_prompt("--- a\n+++ b", "- a.py (изменён)")
        assert "a.py" in user
        assert "```diff" in user
