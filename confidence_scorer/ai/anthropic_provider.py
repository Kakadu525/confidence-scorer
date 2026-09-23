from __future__ import annotations

import os
import threading

from confidence_scorer.ai.base import (
    DEFAULT_MAX_TOKENS,
    FailureTracker,
    describe_api_error,
    extract_json,
    missing_requirement_for,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _downgrade_errors() -> tuple[type[BaseException], ...]:
    try:
        import anthropic
    except ImportError:
        return (TypeError,)
    return (TypeError, anthropic.BadRequestError)


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        timeout: float = 300.0,
        effort: str | None = None,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.timeout = timeout
        self.effort = effort
        self._client = None
        self._client_lock = threading.Lock()
        self._beta_features_supported = True
        self._failures = FailureTracker()

    def failure_reason(self) -> str | None:
        return self._failures.last()

    def missing_requirement(self) -> str | None:
        return missing_requirement_for(
            self.name, self.api_key, self.model, key_env="ANTHROPIC_API_KEY", sdk_module="anthropic"
        )

    @property
    def available(self) -> bool:
        return self.missing_requirement() is None

    def _get_client(self):
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    import anthropic

                    self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout, max_retries=3)
        return self._client

    def _request(self, client, system: str, user: str, max_tokens: int, use_beta: bool):
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user}],
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}

        if not use_beta:
            return client.messages.create(system=system, **kwargs)

        return client.beta.messages.create(
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            betas=[FALLBACK_BETA],
            fallbacks="default",
            **kwargs,
        )

    def complete_json(self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict | list | None:
        self._failures.clear()
        if not self.available:
            return None
        try:
            client = self._get_client()
            try:
                response = self._request(client, system, user, max_tokens, self._beta_features_supported)
            except _downgrade_errors():
                if not self._beta_features_supported:
                    raise
                self._beta_features_supported = False
                response = self._request(client, system, user, max_tokens, use_beta=False)
        except Exception as exc:  # noqa: BLE001
            self._failures.record(describe_api_error(exc, model=self.model, key_env="ANTHROPIC_API_KEY"))
            return None

        if getattr(response, "stop_reason", None) == "refusal":
            self._failures.record("запрос отклонён классификатором безопасности модели (и резервными моделями тоже)")
            return None

        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        parsed = extract_json(text)
        if parsed is None:
            reason = "ответ модели не содержит JSON"
            if getattr(response, "stop_reason", None) == "max_tokens":
                reason += ": ответ обрезан по max_tokens"
            self._failures.record(reason)
        return parsed
