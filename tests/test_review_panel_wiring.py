from __future__ import annotations

import pytest
from pydantic import ValidationError

from confidence_scorer import doctor
from confidence_scorer.checks.review_panel import run_review_panel
from confidence_scorer.config import Config, ReviewerConfig, load_config
from confidence_scorer.doctor import diagnose


def test_single_object_is_panel_of_one():
    cfg = Config(providers={"second_reviewer": {"provider": "anthropic", "model": "claude-opus-5"}})
    assert len(cfg.providers.second_reviewer) == 1
    assert cfg.providers.second_reviewer[0].weight == 1.0


def test_default_is_panel_of_one():
    panel = Config().providers.second_reviewer
    assert len(panel) == 1
    assert panel[0].provider == "openai"


def test_list_form_with_weights_and_labels():
    cfg = Config(
        providers={
            "second_reviewer": [
                {"provider": "anthropic", "model": "claude-opus-5", "weight": 2},
                {"provider": "ollama", "model": "qwen2.5-coder:7b", "label": "qwen-local"},
            ]
        }
    )
    panel = cfg.providers.second_reviewer
    assert [m.display_label for m in panel] == ["anthropic/claude-opus-5", "qwen-local"]
    assert [m.weight for m in panel] == [2.0, 1.0]


def test_yaml_list_form(tmp_path):
    (tmp_path / "confidence.yml").write_text(
        "providers:\n"
        "  second_reviewer:\n"
        "    - { provider: anthropic, model: claude-opus-5, weight: 2 }\n"
        "    - { provider: ollama, model: qwen2.5-coder:7b }\n",
        encoding="utf-8",
    )
    panel = load_config(repo_root=tmp_path).providers.second_reviewer
    assert [m.provider for m in panel] == ["anthropic", "ollama"]


@pytest.mark.parametrize(
    ("panel", "message"),
    [
        ([], "at least one reviewer is required"),
        ([{"provider": "ollama", "model": "m"}, {"provider": "ollama", "model": "m"}], "is listed twice"),
        (
            [
                {"provider": "openai_compatible", "model": "m", "base_url": "http://a/v1"},
                {"provider": "openai_compatible", "model": "m", "base_url": "http://b/v1"},
            ],
            "give the members different labels",
        ),
        ([{"provider": "ollama", "model": "m", "weight": 0}], "greater than 0"),
    ],
)
def test_invalid_panels_are_rejected(panel, message):
    with pytest.raises(ValidationError, match=message):
        Config(providers={"second_reviewer": panel})


class _FakeReviewer:
    def __init__(self, name, answer=None, available=True):
        self.name = name
        self.model = "m"
        self._answer = answer
        self._available = available
        self.calls = 0

    @property
    def available(self):
        return self._available

    def complete_json(self, system, user, *, max_tokens=16000):
        self.calls += 1
        return self._answer


def test_run_review_panel_queries_every_member_once():
    opus = _FakeReviewer("anthropic", {"confidence": 90, "issues": []})
    qwen = _FakeReviewer("ollama", {"confidence": 60, "issues": []})
    members = [
        (ReviewerConfig(provider="anthropic", model="claude-opus-5", weight=2), opus),
        (ReviewerConfig(provider="ollama", model="qwen2.5-coder:7b"), qwen),
    ]
    panel = run_review_panel("diff", [], members, Config())

    assert [m.label for m in panel.members] == ["anthropic/claude-opus-5", "ollama/qwen2.5-coder:7b"]
    assert [(m.provider, m.model, m.weight) for m in panel.members] == [
        ("anthropic", "claude-opus-5", 2.0),
        ("ollama", "qwen2.5-coder:7b", 1.0),
    ]
    assert panel.sub_score == pytest.approx(80.0)
    assert opus.calls == qwen.calls == 1


def test_run_review_panel_isolates_member_failures():
    ok = _FakeReviewer("anthropic", {"confidence": 90})
    down = _FakeReviewer("ollama", None, available=False)
    members = [
        (ReviewerConfig(provider="anthropic", model="claude-opus-5", weight=2), ok),
        (ReviewerConfig(provider="ollama", model="qwen2.5-coder:7b"), down),
    ]
    panel = run_review_panel("diff", [], members, Config())

    assert panel.sub_score == pytest.approx(90.0)
    assert panel.answered_share == pytest.approx(2 / 3)
    assert panel.members[1].result.error
    assert down.calls == 0


@pytest.fixture
def _sdks_installed(monkeypatch):
    monkeypatch.setattr(doctor, "sdk_installed", lambda _name: True)


@pytest.mark.usefixtures("_sdks_installed")
def test_doctor_lists_each_panel_member_and_predicts_cap():
    config = Config(
        providers={
            "second_reviewer": [
                {"provider": "anthropic", "model": "claude-opus-5", "weight": 2},
                {"provider": "ollama", "model": "qwen2.5-coder:7b"},
            ]
        }
    )
    diagnosis = diagnose(config, env={"ANTHROPIC_API_KEY": "x"}, node_ok=True, ollama_probe=lambda _root: [])
    rows = [c for c in diagnosis.checks if c.key == "second_reviewer"]

    assert [c.label for c in rows] == [
        "Second AI reviewer · anthropic/claude-opus-5",
        "Second AI reviewer · ollama/qwen2.5-coder:7b",
    ]
    assert [c.ready for c in rows] == [True, False]
    assert diagnosis.coverage == pytest.approx(0.40 + 0.25 + 0.35 * 2 / 3)


@pytest.mark.usefixtures("_sdks_installed")
def test_doctor_single_reviewer_row_unchanged():
    diagnosis = diagnose(Config(), env={}, node_ok=True)
    rows = [c for c in diagnosis.checks if c.key == "second_reviewer"]
    assert [c.label for c in rows] == ["Second AI reviewer"]
