from __future__ import annotations

from confidence_scorer.ai.base import DEFAULT_MAX_TOKENS, AIProvider
from confidence_scorer.ai.prompts import strategy_generation_prompt
from confidence_scorer.checks.strategy_builder import ALLOWED_KINDS, Spec
from confidence_scorer.extractors.python_extractor import ExtractedFunction


def make_strategy_ai(provider: AIProvider | None):
    if provider is None or not provider.available:
        return None

    def _ask(fn: ExtractedFunction, missing_params: list[str]) -> dict[str, Spec] | None:
        system, user = strategy_generation_prompt(fn, missing_params)
        raw = provider.complete_json(system, user, max_tokens=DEFAULT_MAX_TOKENS)
        if not isinstance(raw, dict):
            return None
        cleaned: dict[str, Spec] = {}
        for name in missing_params:
            spec = raw.get(name)
            if isinstance(spec, dict) and spec.get("kind") in ALLOWED_KINDS:
                cleaned[name] = spec
        return cleaned or None

    return _ask
