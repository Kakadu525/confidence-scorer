from __future__ import annotations

import io
import json
import os
import urllib.error
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from confidence_scorer.ai import base as ai_base
from confidence_scorer.ai import get_provider
from confidence_scorer.ai.base import describe_api_error, failure_reason, looks_truncated
from confidence_scorer.ai.ollama_provider import OllamaProvider, native_root, opener_for
from confidence_scorer.ai.openai_provider import OpenAIProvider
from confidence_scorer.config import Config, ProviderConfig
from confidence_scorer.doctor import diagnose
from confidence_scorer.presets import PRESETS


@pytest.mark.parametrize(
    ("provider", "key_env", "endpoint"),
    [
        ("deepseek", "DEEPSEEK_API_KEY", "https://api.deepseek.com"),
        ("qwen", "DASHSCOPE_API_KEY", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
        ("ollama", None, "http://localhost:11434"),
    ],
)
def test_presets_resolve_endpoint_and_key(provider, key_env, endpoint):
    cfg = ProviderConfig(provider=provider, model="any-model")
    assert cfg.key_env == key_env
    assert cfg.endpoint == endpoint


def test_preset_values_can_be_overridden():
    cfg = ProviderConfig(
        provider="qwen",
        model="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="MY_QWEN_KEY",
    )
    assert cfg.endpoint.startswith("https://dashscope.aliyuncs.com")
    assert cfg.key_env == "MY_QWEN_KEY"


def test_openrouter_accepts_vendor_prefixed_claude_ids():
    assert ProviderConfig(provider="openrouter", model="anthropic/claude-opus-5").model


def test_generic_provider_requires_base_url():
    with pytest.raises(ValidationError, match="base_url"):
        ProviderConfig(provider="openai_compatible", model="local-model")
    cfg = ProviderConfig(provider="openai_compatible", model="local-model", base_url="http://localhost:1234/v1")
    assert cfg.key_env is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"provider": "deepseek", "model": "deepseek-flash", "effort": "low"}, "effort"),
        ({"provider": "deepseek", "model": "deepseek-flash", "context_window": 8192}, "context_window"),
        ({"provider": "anthropic", "model": "claude-opus-5", "base_url": "http://x"}, "base_url"),
    ],
)
def test_settings_that_would_be_silently_ignored_are_rejected(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        ProviderConfig(**kwargs)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x", "localhost:11434"])
def test_base_url_must_be_http(url):
    with pytest.raises(ValidationError, match="http"):
        ProviderConfig(provider="ollama", model="m", base_url=url)


def test_ollama_default_context_is_large_enough():
    assert ProviderConfig(provider="ollama", model="m").context_tokens >= 32768


def test_get_provider_routes_presets():
    ollama = get_provider(ProviderConfig(provider="ollama", model="qwen2.5-coder:7b"))
    assert isinstance(ollama, OllamaProvider)
    assert ollama.context_window == PRESETS["ollama"].context_window

    deepseek = get_provider(ProviderConfig(provider="deepseek", model="deepseek-flash"))
    assert isinstance(deepseek, OpenAIProvider)
    assert deepseek.base_url == "https://api.deepseek.com"
    assert deepseek.key_env == "DEEPSEEK_API_KEY"
    assert deepseek.name == "deepseek"


def test_generic_provider_name_includes_endpoint_for_cache_isolation():
    a = get_provider(ProviderConfig(provider="openai_compatible", model="m", base_url="http://a/v1"))
    b = get_provider(ProviderConfig(provider="openai_compatible", model="m", base_url="http://b/v1"))
    assert a.name != b.name


class _FakeCompletions:
    def __init__(self, response=None, error=None, first_error=None):
        self.calls: list[dict] = []
        self._response = response
        self._error = error
        self._first_error = first_error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._first_error is not None and len(self.calls) == 1:
            raise self._first_error
        if self._error is not None:
            raise self._error
        return self._response


def _chat_response(text: str, prompt_tokens: int | None = None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens),
    )


def _compat_provider(monkeypatch, completions: _FakeCompletions, **kwargs) -> OpenAIProvider:
    monkeypatch.setattr(ai_base, "sdk_installed", lambda _name: True)
    defaults = {"name": "deepseek", "base_url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY"}
    defaults.update(kwargs)
    provider = OpenAIProvider(model="deepseek-flash", api_key="test-key", **defaults)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return provider


def test_compat_provider_clamps_max_tokens(monkeypatch):
    completions = _FakeCompletions(_chat_response('{"ok": 1}', prompt_tokens=10))
    provider = _compat_provider(monkeypatch, completions, max_output_tokens=4096)

    assert provider.complete_json("s", "u") == {"ok": 1}
    assert completions.calls[0]["max_tokens"] == 4096


def test_compat_provider_rejects_truncated_prompt(monkeypatch):
    completions = _FakeCompletions(_chat_response('{"confidence": 100}', prompt_tokens=2_000))
    provider = _compat_provider(monkeypatch, completions)

    assert provider.complete_json("s", "x" * 50_000) is None
    assert "cut off" in provider.failure_reason()


def test_compat_provider_explains_rate_limit(monkeypatch):
    class RateLimitError(Exception):
        pass

    provider = _compat_provider(monkeypatch, _FakeCompletions(error=RateLimitError("429")))

    assert provider.complete_json("s", "u") is None
    assert "max_parallel_ai_calls" in provider.failure_reason()


def test_compat_provider_falls_back_when_json_mode_unsupported(monkeypatch):
    completions = _FakeCompletions(_chat_response('{"ok": 1}'), first_error=TypeError("response_format"))
    provider = _compat_provider(monkeypatch, completions)

    assert provider.complete_json("s", "u") == {"ok": 1}
    assert "response_format" in completions.calls[0]
    assert "response_format" not in completions.calls[1]
    provider.complete_json("s", "u")
    assert "response_format" not in completions.calls[2]


def test_keyless_compat_server_is_available(monkeypatch):
    monkeypatch.setattr(ai_base, "sdk_installed", lambda _name: True)
    provider = OpenAIProvider(model="local", name="openai_compatible:x", base_url="http://x/v1", key_env=None)
    assert provider.available


def test_compat_missing_key_names_the_right_variable(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(ai_base, "sdk_installed", lambda _name: True)
    provider = OpenAIProvider(model="deepseek-flash", name="deepseek", key_env="DEEPSEEK_API_KEY")

    assert not provider.available
    assert "DEEPSEEK_API_KEY" in provider.missing_requirement()


def _ollama(monkeypatch, handler, **kwargs) -> tuple[OllamaProvider, list]:
    calls: list = []
    provider = OllamaProvider(model="qwen2.5-coder:7b", **kwargs)

    def fake_post(path, payload):
        calls.append((path, payload))
        return handler(path, payload)

    monkeypatch.setattr(provider, "_post_json", fake_post)
    return provider, calls


def test_ollama_requests_context_window_natively(monkeypatch):
    provider, calls = _ollama(
        monkeypatch, lambda *_: {"message": {"content": '{"risk_score": 90}'}, "prompt_eval_count": 50}
    )

    assert provider.complete_json("s", "u", max_tokens=16_000) == {"risk_score": 90}
    path, payload = calls[0]
    assert path == "/api/chat"
    assert payload["options"]["num_ctx"] == 32768
    assert payload["format"] == "json"
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0
    assert payload["options"]["seed"] == 0


def test_ollama_rejects_truncated_prompt(monkeypatch):
    provider, _ = _ollama(
        monkeypatch, lambda *_: {"message": {"content": '{"confidence": 100}'}, "prompt_eval_count": 2050}
    )

    assert provider.complete_json("s", "x" * 44_888) is None
    assert "context window" in provider.failure_reason()


def test_ollama_missing_model_suggests_pull(monkeypatch):
    def handler(*_):
        raise urllib.error.HTTPError("http://localhost:11434/api/chat", 404, "not found", {}, io.BytesIO(b"{}"))

    provider, _ = _ollama(monkeypatch, handler)

    assert provider.complete_json("s", "u") is None
    assert "ollama pull qwen2.5-coder:7b" in provider.failure_reason()


def test_ollama_not_running_suggests_serve(monkeypatch):
    def handler(*_):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    provider, _ = _ollama(monkeypatch, handler)

    assert provider.complete_json("s", "u") is None
    assert "ollama serve" in provider.failure_reason()


def test_ollama_needs_neither_key_nor_sdk():
    provider = OllamaProvider(model="m")
    assert provider.available
    assert provider.missing_requirement() is None


@pytest.mark.parametrize(
    ("base_url", "root"),
    [
        (None, "http://localhost:11434"),
        ("http://localhost:11434/v1", "http://localhost:11434"),
        ("http://gpu-box:11434/", "http://gpu-box:11434"),
    ],
)
def test_native_root_accepts_openai_style_url(base_url, root):
    assert native_root(base_url) == root


def _active_proxies(opener) -> dict:
    proxies: dict = {}
    for handler in opener.handlers:
        proxies.update(getattr(handler, "proxies", {}) or {})
    return proxies


def test_localhost_bypasses_system_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    assert _active_proxies(opener_for("http://localhost:11434/api/chat")) == {}
    assert "http" in _active_proxies(opener_for("http://gpu-box.example:11434/api/chat"))


@pytest.mark.parametrize(
    ("chars", "tokens", "expected"),
    [
        (44_888, 2_050, True),
        (44_888, 12_000, False),
        (1_000, 10, False),
        (50_000, None, False),
    ],
)
def test_looks_truncated(chars, tokens, expected):
    assert looks_truncated(chars, tokens) is expected


def test_describe_api_error_is_actionable():
    class AuthenticationError(Exception):
        pass

    assert "DEEPSEEK_API_KEY" in describe_api_error(AuthenticationError("401"), model="m", key_env="DEEPSEEK_API_KEY")


def test_failure_reason_is_per_thread(monkeypatch):
    import threading

    provider, _ = _ollama(monkeypatch, lambda *_: {"message": {"content": "не json"}})
    provider.complete_json("s", "u")
    other_thread_reason = []
    thread = threading.Thread(target=lambda: other_thread_reason.append(failure_reason(provider)))
    thread.start()
    thread.join()

    assert "JSON" in failure_reason(provider)
    assert other_thread_reason == [None]


def _ollama_config(model="qwen2.5-coder:7b") -> Config:
    return Config(
        providers={
            "semantic_diff": {"provider": "ollama", "model": model},
            "second_reviewer": {"provider": "ollama", "model": model},
            "strategy_generation": {"provider": "ollama", "model": model},
        }
    )


def test_doctor_ollama_ready_without_any_keys():
    diagnosis = diagnose(_ollama_config(), env={}, node_ok=True, ollama_probe=lambda _root: ["qwen2.5-coder:7b"])

    assert all(c.ready for c in diagnosis.checks)
    assert diagnosis.score_cap is None


def test_doctor_ollama_not_running():
    diagnosis = diagnose(_ollama_config(), env={}, node_ok=True, ollama_probe=lambda _root: None)
    semantic = next(c for c in diagnosis.checks if c.key == "semantic_diff")

    assert not semantic.ready
    assert "ollama serve" in semantic.detail


def test_doctor_ollama_model_not_pulled():
    diagnosis = diagnose(_ollama_config("qwen3-coder:30b"), env={}, node_ok=True, ollama_probe=lambda _root: ["x:7b"])
    semantic = next(c for c in diagnosis.checks if c.key == "semantic_diff")

    assert "ollama pull qwen3-coder:30b" in semantic.detail


def test_doctor_matches_untagged_model_to_latest():
    diagnosis = diagnose(_ollama_config("llama3"), env={}, node_ok=True, ollama_probe=lambda _root: ["llama3:latest"])
    assert all(c.ready for c in diagnosis.checks if c.key in ("semantic_diff", "second_reviewer"))


LIVE_MODEL = os.environ.get("CONFIDENCE_OLLAMA_MODEL")


@pytest.mark.skipif(not LIVE_MODEL, reason="задайте CONFIDENCE_OLLAMA_MODEL, чтобы проверить на живой Ollama")
def test_live_ollama_sees_the_start_of_a_long_prompt():
    filler = "\n".join(f"def helper_{i}(x: int) -> int:\n    return x + {i}" for i in range(900))
    prompt = f"Секретное слово: ЯШМА.\n{filler}\n\nОтветь JSON: {{\"secret\": \"<секретное слово из начала>\"}}"
    provider = OllamaProvider(model=LIVE_MODEL)

    result = provider.complete_json("Отвечай только JSON.", prompt, max_tokens=64)

    assert result is not None, failure_reason(provider)
    assert "ЯШМА" in json.dumps(result, ensure_ascii=False)
