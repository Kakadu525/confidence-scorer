from __future__ import annotations

import copy
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from confidence_scorer.presets import GENERIC_OPENAI_COMPATIBLE, PRESETS, ProviderPreset

Provider = Literal["anthropic", "openai", "openai_compatible", "ollama", "deepseek", "qwen", "openrouter"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]

DEFAULT_CONFIG_FILENAMES = ("confidence.yml", ".confidence.yml", "confidence.yaml")

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_OPENAI_MODEL = "gpt-4.1"

API_KEY_ENV_BY_PROVIDER = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


class ProviderConfig(BaseModel):
    provider: Provider = "anthropic"
    model: str = DEFAULT_ANTHROPIC_MODEL
    effort: Effort | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    max_output_tokens: int | None = Field(default=None, gt=0)
    context_window: int | None = Field(default=None, gt=0)

    @field_validator("base_url")
    @classmethod
    def _http_only(cls, v: str | None) -> str | None:
        if v is not None and not v.lower().startswith(("http://", "https://")):
            raise ValueError(f"base_url должен начинаться с http:// или https://, получено: {v!r}")
        return v

    @model_validator(mode="after")
    def _check_consistency(self) -> ProviderConfig:
        is_claude = self.model.startswith("claude-")
        if self.provider == "openai" and is_claude:
            raise ValueError(
                f"model '{self.model}' это модель Claude, а provider: openai. "
                f"Поставьте provider: anthropic или укажите модель OpenAI (например, {DEFAULT_OPENAI_MODEL})."
            )
        if self.provider == "anthropic":
            if not is_claude:
                raise ValueError(
                    f"model '{self.model}' не похожа на модель Claude (ID начинаются с 'claude-'), а provider: anthropic. "
                    f"Для Anthropic используйте, например, {DEFAULT_ANTHROPIC_MODEL}."
                )
            if self.base_url or self.max_output_tokens:
                raise ValueError(
                    "base_url и max_output_tokens поддерживаются только для OpenAI-совместимых провайдеров, "
                    "а не для provider: anthropic."
                )
        if self.effort and self.provider != "anthropic":
            raise ValueError(f"effort действует только для provider: anthropic, а здесь provider: {self.provider}.")
        if self.context_window and self.provider != "ollama":
            raise ValueError("context_window задаётся только для provider: ollama, у облачных API окно фиксировано.")
        if self.provider == GENERIC_OPENAI_COMPATIBLE and not self.base_url:
            raise ValueError(
                "для provider: openai_compatible нужен base_url, адрес OpenAI-совместимого API "
                "(например, https://example.com/v1)."
            )
        return self

    @property
    def preset(self) -> ProviderPreset | None:
        return PRESETS.get(self.provider)

    @property
    def key_env(self) -> str | None:
        if self.api_key_env:
            return self.api_key_env
        if self.provider in API_KEY_ENV_BY_PROVIDER:
            return API_KEY_ENV_BY_PROVIDER[self.provider]
        return self.preset.api_key_env if self.preset else None

    @property
    def endpoint(self) -> str | None:
        if self.base_url:
            return self.base_url
        return self.preset.base_url if self.preset else None

    @property
    def output_token_cap(self) -> int | None:
        if self.max_output_tokens:
            return self.max_output_tokens
        return self.preset.max_output_tokens if self.preset else None

    @property
    def context_tokens(self) -> int | None:
        if self.context_window:
            return self.context_window
        return self.preset.context_window if self.preset else None

    @property
    def sdk_module(self) -> str | None:
        if self.provider == "anthropic":
            return "anthropic"
        if self.provider == "ollama":
            return None
        return "openai"


class ReviewerConfig(ProviderConfig):
    weight: float = Field(default=1.0, gt=0)
    label: str | None = None

    @property
    def display_label(self) -> str:
        return self.label or f"{self.provider}/{self.model}"


class ProvidersConfig(BaseModel):
    strategy_generation: ProviderConfig = Field(default_factory=ProviderConfig)
    semantic_diff: ProviderConfig = Field(default_factory=ProviderConfig)
    second_reviewer: list[ReviewerConfig] = Field(
        default_factory=lambda: [ReviewerConfig(provider="openai", model=DEFAULT_OPENAI_MODEL)]
    )

    @field_validator("second_reviewer", mode="before")
    @classmethod
    def _single_reviewer_is_panel_of_one(cls, value):
        if isinstance(value, (dict, ProviderConfig)):
            return [value]
        return value

    @field_validator("second_reviewer")
    @classmethod
    def _check_panel(cls, panel: list[ReviewerConfig]) -> list[ReviewerConfig]:
        if not panel:
            raise ValueError("second_reviewer: нужен хотя бы один ревьюер")
        seen: set[tuple[str, str, str | None]] = set()
        labels: set[str] = set()
        for member in panel:
            key = (member.provider, member.model, member.endpoint)
            if key in seen:
                raise ValueError(
                    f"second_reviewer: {member.display_label} указан дважды: дубликат молча удвоил бы вес модели"
                )
            seen.add(key)
            if member.display_label in labels:
                raise ValueError(
                    f"second_reviewer: имя '{member.display_label}' встречается дважды, "
                    f"задайте участникам разные label"
                )
            labels.add(member.display_label)
        return panel


class WeightsConfig(BaseModel):
    property_tests: float = 0.40
    semantic_diff: float = 0.25
    second_reviewer: float = 0.35

    @model_validator(mode="after")
    def _normalize(self) -> WeightsConfig:
        total = self.property_tests + self.semantic_diff + self.second_reviewer
        if total <= 0:
            raise ValueError("Сумма весов (weights) должна быть больше нуля")
        if abs(total - 1.0) > 1e-6:
            self.property_tests /= total
            self.semantic_diff /= total
            self.second_reviewer /= total
        return self


class ThresholdsConfig(BaseModel):
    pass_at: int = 85
    warn_below: int = 65
    fail_below: int = 40

    @model_validator(mode="after")
    def _check_order(self) -> ThresholdsConfig:
        if not (0 <= self.fail_below <= self.warn_below <= self.pass_at <= 100):
            raise ValueError(
                "Пороги должны соблюдать порядок: 0 <= fail_below <= warn_below <= pass_at <= 100"
            )
        return self


class HardFailConfig(BaseModel):
    enabled: bool = True
    cap_score_on_confirmed_counterexample: int = 35


class HypothesisConfig(BaseModel):
    max_examples: int = 50
    per_function_timeout_s: int = 10
    total_budget_s: int = 180
    seed: int | None = 0
    per_file_timeout_s: int = 60


class JsConfig(BaseModel):
    enabled: bool = True
    node_binary: str = "node"
    fast_check_examples: int = 30
    per_function_timeout_s: int = 15
    seed: int | None = 0


class LimitsConfig(BaseModel):
    max_functions_per_run: int = 25
    max_diff_bytes_to_ai: int = 60_000
    max_full_diff_bytes: int = 120_000
    max_parallel_workers: int = 4
    max_parallel_ai_calls: int = 4


class EvidenceConfig(BaseModel):
    enabled: bool = True
    min_cap: int = 50

    @model_validator(mode="after")
    def _check_range(self) -> EvidenceConfig:
        if not 0 <= self.min_cap <= 100:
            raise ValueError("evidence.min_cap должен быть в диапазоне 0..100")
        return self


class CacheConfig(BaseModel):
    enabled: bool = True
    dir: str = ".confidence_cache"
    ttl_s: int = 14 * 24 * 3600


class GitHubConfig(BaseModel):
    post_comment: bool = True
    comment_marker: str = "<!-- confidence-scorer:report -->"


class Config(BaseModel):
    languages: list[Literal["python", "javascript"]] = Field(
        default_factory=lambda: ["python", "javascript"]
    )
    exclude: list[str] = Field(
        default_factory=lambda: [
            "**/tests/**",
            "**/test_*.py",
            "**/*_test.py",
            "**/*.min.js",
            "**/node_modules/**",
            "**/dist/**",
            "**/build/**",
            "**/migrations/**",
            "**/vendor/**",
        ]
    )
    weights: WeightsConfig = Field(default_factory=WeightsConfig)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    hard_fail: HardFailConfig = Field(default_factory=HardFailConfig)
    hypothesis: HypothesisConfig = Field(default_factory=HypothesisConfig)
    js: JsConfig = Field(default_factory=JsConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    execute_changed_code: bool = True

    @field_validator("exclude")
    @classmethod
    def _non_empty_globs(cls, v: list[str]) -> list[str]:
        return [g for g in v if g.strip()]


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def find_config_file(start_dir: Path) -> Path | None:
    for name in DEFAULT_CONFIG_FILENAMES:
        candidate = start_dir / name
        if candidate.is_file():
            return candidate
    return None


def load_config(path: str | Path | None = None, *, repo_root: str | Path = ".") -> Config:
    repo_root = Path(repo_root)
    config_path = Path(path) if path else find_config_file(repo_root)

    if config_path is None or not config_path.is_file():
        return Config()

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path}: конфиг должен быть YAML-словарём")

    return Config(**raw)


DEFAULT_CONFIG_TEMPLATE = """\
# Настройки confidence-scorer. Все параметры и значения по умолчанию: confidence_scorer/config.py

languages: [python, javascript]

exclude:
  - "**/tests/**"
  - "**/test_*.py"
  - "**/*_test.py"
  - "**/*.min.js"
  - "**/node_modules/**"
  - "**/dist/**"
  - "**/build/**"
  - "**/migrations/**"
  - "**/vendor/**"

weights:
  property_tests: 0.40
  semantic_diff: 0.25
  second_reviewer: 0.35

thresholds:
  pass_at: 85
  warn_below: 65
  fail_below: 40

hard_fail:
  enabled: true
  cap_score_on_confirmed_counterexample: 35

hypothesis:
  max_examples: 50
  per_function_timeout_s: 10
  per_file_timeout_s: 60
  total_budget_s: 180
  seed: 0

js:
  enabled: true
  node_binary: node
  fast_check_examples: 30
  per_function_timeout_s: 15
  seed: 0

limits:
  max_functions_per_run: 25
  max_diff_bytes_to_ai: 60000
  max_full_diff_bytes: 120000
  max_parallel_workers: 4
  max_parallel_ai_calls: 4    # на бесплатных тарифах и локальной Ollama поставьте 1

evidence:
  enabled: true
  min_cap: 50

# Добавьте каталог кэша в .gitignore.
cache:
  enabled: true
  dir: ".confidence_cache"
  ttl_s: 1209600

# Ключи в этот файл не пишутся, их берут из переменных окружения:
#   anthropic   ANTHROPIC_API_KEY
#   openai      OPENAI_API_KEY
#   deepseek    DEEPSEEK_API_KEY
#   qwen        DASHSCOPE_API_KEY
#   openrouter  OPENROUTER_API_KEY
#   ollama      ключ не нужен
# Для API нужен ключ с пополненным балансом, подписка Claude Pro/Max или ChatGPT Plus его не даёт.
providers:
  strategy_generation:
    provider: anthropic
    model: claude-opus-5

  semantic_diff:
    provider: anthropic
    model: claude-opus-5
    # effort: medium      # раскомментируйте, чтобы снизить стоимость

  second_reviewer:
    provider: openai
    model: gpt-4.1        # проверьте, что модель доступна в вашем аккаунте OpenAI
  # Если есть только ключ Anthropic, замените блок выше на:
  # second_reviewer:
  #   provider: anthropic
  #   model: claude-opus-5

  # Панель из нескольких ревьюеров:
  # second_reviewer:
  #   - { provider: anthropic, model: claude-opus-5,    weight: 2 }
  #   - { provider: ollama,    model: qwen2.5-coder:7b, weight: 1 }
  #   - { provider: deepseek,  model: deepseek-flash,   weight: 1, label: deepseek }

  # Бесплатно и локально через Ollama (сначала выполните ollama pull qwen2.5-coder:7b):
  # second_reviewer:
  #   provider: ollama
  #   model: qwen2.5-coder:7b
  #
  # DeepSeek:
  # second_reviewer:
  #   provider: deepseek
  #   model: deepseek-flash
  #
  # OpenRouter, бесплатные модели с суффиксом ":free":
  # second_reviewer:
  #   provider: openrouter
  #   model: <id модели>:free
  #
  # Другой OpenAI-совместимый сервер (LM Studio, vLLM):
  # second_reviewer:
  #   provider: openai_compatible
  #   base_url: http://localhost:1234/v1
  #   model: <имя модели на сервере>
  #   # api_key_env: MY_SERVER_KEY   # если сервер требует ключ

github:
  post_comment: true
  comment_marker: "<!-- confidence-scorer:report -->"

# В недоверенном окружении поставьте false, чтобы код из диффа не запускался.
execute_changed_code: true
"""


def write_default_config(path: Path) -> None:
    path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
