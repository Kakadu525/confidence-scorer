from __future__ import annotations

import importlib.util
import json
import re
import threading
from typing import Protocol

DEFAULT_MAX_TOKENS = 16_000


class AIProvider(Protocol):
    name: str
    model: str

    @property
    def available(self) -> bool: ...

    def complete_json(
        self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> dict | list | None: ...


_MAX_PLAUSIBLE_CHARS_PER_TOKEN = 8
_TRUNCATION_CHECK_MIN_CHARS = 4000


def looks_truncated(prompt_chars: int, prompt_tokens: int | None) -> bool:
    if not prompt_tokens or prompt_chars < _TRUNCATION_CHECK_MIN_CHARS:
        return False
    return prompt_chars / prompt_tokens > _MAX_PLAUSIBLE_CHARS_PER_TOKEN


def describe_api_error(exc: BaseException, *, model: str, endpoint: str | None = None, key_env: str | None = None) -> str:
    kind = type(exc).__name__
    where = f" ({endpoint})" if endpoint else ""
    if kind == "RateLimitError":
        return (
            "превышен лимит запросов (429). На бесплатных тарифах уменьшите "
            "limits.max_parallel_ai_calls до 1 или дождитесь сброса дневного лимита"
        )
    if kind == "AuthenticationError":
        return f"ключ отклонён сервером{where}" + (f", проверьте {key_env}" if key_env else "")
    if kind == "PermissionDeniedError":
        return f"у ключа нет доступа к модели {model}{where}"
    if kind == "NotFoundError":
        return f"модель {model} не найдена{where}"
    if kind in ("APIConnectionError", "APITimeoutError", "ConnectionError", "URLError", "TimeoutError"):
        return f"сервер недоступен{where}: {exc}"
    return f"{kind}: {str(exc)[:200]}"


class FailureTracker:
    def __init__(self) -> None:
        self._local = threading.local()

    def clear(self) -> None:
        self._local.reason = None

    def record(self, reason: str) -> None:
        self._local.reason = reason

    def last(self) -> str | None:
        return getattr(self._local, "reason", None)


def failure_reason(provider: object | None) -> str | None:
    getter = getattr(provider, "failure_reason", None)
    return getter() if callable(getter) else None


def sdk_installed(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def missing_requirement_for(
    provider_name: str,
    api_key: str | None,
    model: str,
    *,
    key_env: str | None,
    sdk_module: str | None,
) -> str | None:
    if key_env and not api_key:
        return f"не задана переменная окружения {key_env} (provider: {provider_name}, model: {model})"
    if sdk_module and not sdk_installed(sdk_module):
        return f"не установлен Python-пакет {sdk_module}: pip install \"confidence-scorer[{sdk_module}]\""
    return None


def unavailable_reason(provider: AIProvider | None) -> str:
    if provider is None:
        return "AI-провайдер не настроен"
    missing = getattr(provider, "missing_requirement", None)
    detail = missing() if callable(missing) else None
    if not detail:
        from confidence_scorer.config import API_KEY_ENV_BY_PROVIDER

        name = getattr(provider, "name", "?")
        env = API_KEY_ENV_BY_PROVIDER.get(name, "API-ключа")
        detail = f"не задана переменная окружения {env} (provider: {name}, model: {getattr(provider, 'model', '?')})"
    return f"AI-провайдер недоступен: {detail}"


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)
_FIRST_OBJECT_RE = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


def extract_json(text: str) -> dict | list | None:
    if not text:
        return None

    candidates = []
    fenced = _JSON_BLOCK_RE.findall(text)
    candidates.extend(fenced)
    candidates.append(text.strip())
    match = _FIRST_OBJECT_RE.search(text)
    if match:
        candidates.append(match.group(1))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    return None
