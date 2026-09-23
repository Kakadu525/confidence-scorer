from __future__ import annotations

from confidence_scorer.ai.base import DEFAULT_MAX_TOKENS, AIProvider, failure_reason
from confidence_scorer.ai.cache import ResponseCache
from confidence_scorer.config import ProviderConfig


class CachingProvider:
    def __init__(self, inner: AIProvider, cache: ResponseCache):
        self._inner = inner
        self._cache = cache

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def available(self) -> bool:
        return self._inner.available

    def missing_requirement(self) -> str | None:
        missing = getattr(self._inner, "missing_requirement", None)
        return missing() if callable(missing) else None

    def failure_reason(self) -> str | None:
        return failure_reason(self._inner)

    def complete_json(self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS):
        if not self.available:
            return None
        effort = getattr(self._inner, "effort", None)
        model_key = f"{self._inner.model}@effort={effort}" if effort else self._inner.model
        key = ResponseCache.make_key(self._inner.name, model_key, system, user)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self._inner.complete_json(system, user, max_tokens=max_tokens)
        self._cache.set(key, result)
        return result


def get_provider(cfg: ProviderConfig, cache: ResponseCache | None = None) -> AIProvider:
    if cfg.provider == "anthropic":
        from confidence_scorer.ai.anthropic_provider import AnthropicProvider

        provider: AIProvider = AnthropicProvider(model=cfg.model, effort=cfg.effort)
    elif cfg.provider == "ollama":
        from confidence_scorer.ai.ollama_provider import OllamaProvider

        provider = OllamaProvider(
            model=cfg.model,
            base_url=cfg.endpoint,
            context_window=cfg.context_tokens,
            max_output_tokens=cfg.output_token_cap,
        )
    else:
        from confidence_scorer.ai.openai_provider import OpenAIProvider

        name = cfg.provider if cfg.provider != "openai_compatible" else f"openai_compatible:{cfg.endpoint}"
        provider = OpenAIProvider(
            model=cfg.model,
            name=name,
            base_url=cfg.endpoint,
            key_env=cfg.key_env,
            max_output_tokens=cfg.output_token_cap,
        )

    if cache is not None and cache.enabled:
        return CachingProvider(provider, cache)
    return provider


__all__ = ["AIProvider", "CachingProvider", "ResponseCache", "get_provider"]
