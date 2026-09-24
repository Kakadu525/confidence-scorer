from __future__ import annotations

import os
import threading

from confidence_scorer.ai.base import (
    DEFAULT_MAX_TOKENS,
    FailureTracker,
    describe_api_error,
    extract_json,
    looks_truncated,
    missing_requirement_for,
)
from confidence_scorer.i18n import tr

_KEYLESS_PLACEHOLDER = "not-needed"


def _downgrade_errors() -> tuple[type[BaseException], ...]:
    try:
        import openai
    except ImportError:
        return (TypeError,)
    return (TypeError, openai.BadRequestError, openai.UnprocessableEntityError)


class OpenAIProvider:
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        timeout: float = 300.0,
        *,
        name: str = "openai",
        base_url: str | None = None,
        key_env: str | None = "OPENAI_API_KEY",
        max_output_tokens: int | None = None,
    ):
        self.name = name
        self.model = model
        self.base_url = base_url
        self.key_env = key_env
        self.api_key = api_key or (os.environ.get(key_env) if key_env else None)
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self._client = None
        self._client_lock = threading.Lock()
        self._json_mode_supported = True
        self._failures = FailureTracker()

    def failure_reason(self) -> str | None:
        return self._failures.last()

    def missing_requirement(self) -> str | None:
        return missing_requirement_for(
            self.name, self.api_key, self.model, key_env=self.key_env, sdk_module="openai"
        )

    @property
    def available(self) -> bool:
        return self.missing_requirement() is None

    def _get_client(self):
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    import openai

                    self._client = openai.OpenAI(
                        api_key=self.api_key or _KEYLESS_PLACEHOLDER,
                        base_url=self.base_url,
                        timeout=self.timeout,
                        max_retries=3,
                    )
        return self._client

    def _effective_max_tokens(self, requested: int) -> int:
        return min(requested, self.max_output_tokens) if self.max_output_tokens else requested

    def complete_json(self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict | list | None:
        self._failures.clear()
        if not self.available:
            return None
        kwargs = {
            "model": self.model,
            "max_tokens": self._effective_max_tokens(max_tokens),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            client = self._get_client()
            if self._json_mode_supported:
                try:
                    response = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
                except _downgrade_errors():
                    self._json_mode_supported = False
                    response = client.chat.completions.create(**kwargs)
            else:
                response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            self._failures.record(
                describe_api_error(exc, model=self.model, endpoint=self.base_url, key_env=self.key_env)
            )
            return None

        if not response.choices:
            self._failures.record(tr("the server returned an empty answer", "сервер вернул пустой ответ"))
            return None

        usage = getattr(response, "usage", None)
        if looks_truncated(len(system) + len(user), getattr(usage, "prompt_tokens", None)):
            self._failures.record(
                tr(
                    "the server cut off part of the prompt (the model did not see all of the code): "
                    "increase the model's context window on the server",
                    "сервер отрезал часть промпта (модель видела не весь код): "
                    "увеличьте окно контекста модели на сервере",
                )
            )
            return None

        text = response.choices[0].message.content or ""
        parsed = extract_json(text)
        if parsed is None:
            self._failures.record(tr("the model's answer contains no JSON", "ответ модели не содержит JSON"))
        return parsed
