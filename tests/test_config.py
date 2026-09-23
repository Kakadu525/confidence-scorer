import pytest
from pydantic import ValidationError

from confidence_scorer.config import Config, ThresholdsConfig, WeightsConfig, load_config, write_default_config


def test_default_config_is_valid():
    cfg = Config()
    assert cfg.languages == ["python", "javascript"]
    assert abs(sum([cfg.weights.property_tests, cfg.weights.semantic_diff, cfg.weights.second_reviewer]) - 1.0) < 1e-9


def test_weights_are_normalized():
    w = WeightsConfig(property_tests=2, semantic_diff=1, second_reviewer=1)
    assert abs(w.property_tests - 0.5) < 1e-9
    assert abs(w.semantic_diff - 0.25) < 1e-9


def test_weights_reject_zero_sum():
    with pytest.raises(ValidationError):
        WeightsConfig(property_tests=0, semantic_diff=0, second_reviewer=0)


def test_thresholds_must_be_ordered():
    ThresholdsConfig(fail_below=10, warn_below=50, pass_at=90)
    with pytest.raises(ValidationError):
        ThresholdsConfig(fail_below=60, warn_below=50, pass_at=90)


def test_load_config_missing_file_uses_defaults(tmp_path):
    cfg = load_config(repo_root=tmp_path)
    assert cfg == Config()


def test_load_config_reads_yaml_overrides(tmp_path):
    (tmp_path / "confidence.yml").write_text(
        "languages: [python]\nthresholds:\n  pass_at: 90\n  warn_below: 60\n  fail_below: 30\n",
        encoding="utf-8",
    )
    cfg = load_config(repo_root=tmp_path)
    assert cfg.languages == ["python"]
    assert cfg.thresholds.pass_at == 90


def test_write_default_config_round_trips(tmp_path):
    path = tmp_path / "confidence.yml"
    write_default_config(path)
    cfg = load_config(path=path)
    assert isinstance(cfg, Config)


def test_default_template_matches_code_defaults(tmp_path):
    path = tmp_path / "confidence.yml"
    write_default_config(path)
    assert load_config(path=path) == Config()


def test_default_models_are_current_generation():
    from confidence_scorer.config import DEFAULT_ANTHROPIC_MODEL

    providers = Config().providers
    assert providers.semantic_diff.model == DEFAULT_ANTHROPIC_MODEL
    assert providers.strategy_generation.model == DEFAULT_ANTHROPIC_MODEL
    assert "claude-sonnet-4-5" not in {
        providers.semantic_diff.model,
        providers.strategy_generation.model,
        providers.second_reviewer[0].model,
    }


def test_second_reviewer_uses_different_provider_by_default():
    providers = Config().providers
    assert providers.second_reviewer[0].provider != providers.semantic_diff.provider


@pytest.mark.parametrize(
    ("provider", "model", "hint"),
    [("openai", "claude-opus-5", "модель Claude"), ("anthropic", "gpt-4.1", "не похожа на модель Claude")],
)
def test_provider_model_mismatch_is_rejected_with_hint(provider, model, hint):
    from confidence_scorer.config import ProviderConfig

    with pytest.raises(ValidationError, match=hint):
        ProviderConfig(provider=provider, model=model)


def test_effort_accepts_only_known_levels():
    from confidence_scorer.config import ProviderConfig

    assert ProviderConfig(effort="medium").effort == "medium"
    with pytest.raises(ValidationError):
        ProviderConfig(effort="turbo")


def test_api_key_env_matches_provider():
    from confidence_scorer.config import ProviderConfig

    assert ProviderConfig(provider="anthropic", model="claude-opus-5").key_env == "ANTHROPIC_API_KEY"
    assert ProviderConfig(provider="openai", model="gpt-4.1").key_env == "OPENAI_API_KEY"
