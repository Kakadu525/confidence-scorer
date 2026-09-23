from __future__ import annotations

from types import SimpleNamespace

import pytest

from confidence_scorer.ai import base as ai_base
from confidence_scorer.ai.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from confidence_scorer.ai.base import DEFAULT_MAX_TOKENS, unavailable_reason


def _response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


class _Endpoint:
    def __init__(self, response=None, error: BaseException | None = None):
        self.calls: list[dict] = []
        self._response = response
        self._error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class _FakeClient:
    def __init__(self, beta_endpoint: _Endpoint, plain_endpoint: _Endpoint):
        self.beta = SimpleNamespace(messages=beta_endpoint)
        self.messages = plain_endpoint


def _provider(monkeypatch, client: _FakeClient, **kwargs) -> AnthropicProvider:
    monkeypatch.setattr(ai_base, "sdk_installed", lambda _name: True)
    provider = AnthropicProvider(model="claude-opus-5", api_key="test-key", **kwargs)
    provider._client = client
    return provider


def test_default_request_uses_fallbacks_and_cacheable_system(monkeypatch):
    beta = _Endpoint(_response('{"risk_score": 90}'))
    provider = _provider(monkeypatch, _FakeClient(beta, _Endpoint()))

    assert provider.complete_json("system prompt", "user prompt") == {"risk_score": 90}

    (call,) = beta.calls
    assert call["model"] == "claude-opus-5"
    assert call["fallbacks"] == "default"
    assert call["betas"] == [FALLBACK_BETA]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["system"][0]["text"] == "system prompt"
    assert "output_config" not in call
    assert call["max_tokens"] == DEFAULT_MAX_TOKENS >= 16_000
    assert "thinking" not in call


def test_effort_is_sent_when_configured(monkeypatch):
    beta = _Endpoint(_response("{}"))
    provider = _provider(monkeypatch, _FakeClient(beta, _Endpoint()), effort="medium")

    provider.complete_json("s", "u")

    assert beta.calls[0]["output_config"] == {"effort": "medium"}


def test_refusal_is_not_parsed_as_an_answer(monkeypatch):
    beta = _Endpoint(_response('{"risk_score": 100}', stop_reason="refusal"))
    provider = _provider(monkeypatch, _FakeClient(beta, _Endpoint()))

    assert provider.complete_json("s", "u") is None


def test_downgrades_to_plain_request_when_beta_params_are_rejected(monkeypatch):
    beta = _Endpoint(error=TypeError("unexpected keyword argument 'fallbacks'"))
    plain = _Endpoint(_response('{"confidence": 80}'))
    provider = _provider(monkeypatch, _FakeClient(beta, plain))

    assert provider.complete_json("s", "u") == {"confidence": 80}
    assert plain.calls[0]["system"] == "s"
    assert "fallbacks" not in plain.calls[0]

    provider.complete_json("s", "u")
    assert len(beta.calls) == 1
    assert len(plain.calls) == 2


def test_network_failure_degrades_to_none_without_disabling_beta(monkeypatch):
    beta = _Endpoint(error=ConnectionError("сеть недоступна"))
    provider = _provider(monkeypatch, _FakeClient(beta, _Endpoint()))

    assert provider.complete_json("s", "u") is None
    assert provider._beta_features_supported is True


def test_missing_key_is_named_explicitly(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProvider(model="claude-opus-5")

    assert provider.available is False
    reason = unavailable_reason(provider)
    assert "ANTHROPIC_API_KEY" in reason
    assert "claude-opus-5" in reason


def test_missing_sdk_is_reported_instead_of_bad_answer(monkeypatch):
    monkeypatch.setattr(ai_base, "sdk_installed", lambda _name: False)
    provider = AnthropicProvider(model="claude-opus-5", api_key="test-key")

    assert provider.available is False
    reason = unavailable_reason(provider)
    assert "confidence-scorer[anthropic]" in reason


@pytest.mark.parametrize("effort", [None, "low"])
def test_cache_key_distinguishes_effort(monkeypatch, tmp_path, effort):
    from confidence_scorer.ai import CachingProvider, ResponseCache

    beta = _Endpoint(_response('{"ok": true}'))
    inner = _provider(monkeypatch, _FakeClient(beta, _Endpoint()), effort=effort)
    cache = ResponseCache(tmp_path, ttl_s=0)
    cached = CachingProvider(inner, cache)

    cached.complete_json("s", "u")
    cached.complete_json("s", "u")
    assert len(beta.calls) == 1

    other = _provider(monkeypatch, _FakeClient(_Endpoint(_response('{"ok": false}')), _Endpoint()),
                      effort="max")
    assert CachingProvider(other, cache).complete_json("s", "u") == {"ok": False}
