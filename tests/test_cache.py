from __future__ import annotations

from confidence_scorer.ai import CachingProvider
from confidence_scorer.ai.cache import ResponseCache


class _CountingProvider:
    name = "fake"
    model = "fake-1"

    _DEFAULT = object()

    def __init__(self, payload=_DEFAULT, available=True):
        self.calls = 0
        self._payload = {"risk_score": 100} if payload is self._DEFAULT else payload
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def complete_json(self, system: str, user: str, *, max_tokens: int = 1500):
        self.calls += 1
        return self._payload


def test_second_identical_call_is_served_from_cache(tmp_path):
    inner = _CountingProvider()
    provider = CachingProvider(inner, ResponseCache(tmp_path, ttl_s=3600))

    first = provider.complete_json("sys", "user")
    second = provider.complete_json("sys", "user")

    assert first == second == {"risk_score": 100}
    assert inner.calls == 1


def test_different_prompt_is_not_cached_together(tmp_path):
    inner = _CountingProvider()
    provider = CachingProvider(inner, ResponseCache(tmp_path, ttl_s=3600))

    provider.complete_json("sys", "функция А")
    provider.complete_json("sys", "функция Б")

    assert inner.calls == 2


def test_key_depends_on_model_so_model_change_invalidates(tmp_path):
    cache = ResponseCache(tmp_path, ttl_s=3600)
    a = ResponseCache.make_key("anthropic", "model-1", "sys", "user")
    b = ResponseCache.make_key("anthropic", "model-2", "sys", "user")
    c = ResponseCache.make_key("openai", "model-1", "sys", "user")

    assert len({a, b, c}) == 3
    assert cache.get(a) is None


def test_expired_entry_is_ignored(tmp_path):
    import json
    import time

    cache = ResponseCache(tmp_path, ttl_s=60)
    key = ResponseCache.make_key("p", "m", "s", "u")
    cache.set(key, {"x": 1})
    assert cache.get(key) == {"x": 1}

    stored = next(tmp_path.rglob("*.json"))
    stored.write_text(
        json.dumps({"stored_at": time.time() - 3600, "value": {"x": 1}}), encoding="utf-8"
    )

    assert cache.get(key) is None


def test_zero_ttl_means_no_expiration(tmp_path):
    cache = ResponseCache(tmp_path, ttl_s=0)
    key = ResponseCache.make_key("p", "m", "s", "u")
    cache.set(key, {"x": 1})

    assert cache.get(key) == {"x": 1}


def test_disabled_cache_never_stores(tmp_path):
    inner = _CountingProvider()
    cache = ResponseCache(tmp_path, ttl_s=3600, enabled=False)
    provider = CachingProvider(inner, cache)

    provider.complete_json("sys", "user")
    provider.complete_json("sys", "user")

    assert inner.calls == 2
    assert not list(tmp_path.rglob("*.json"))


def test_none_result_is_not_cached(tmp_path):
    inner = _CountingProvider(payload=None)
    provider = CachingProvider(inner, ResponseCache(tmp_path, ttl_s=3600))

    provider.complete_json("sys", "user")
    provider.complete_json("sys", "user")

    assert inner.calls == 2


def test_unavailable_provider_short_circuits(tmp_path):
    inner = _CountingProvider(available=False)
    provider = CachingProvider(inner, ResponseCache(tmp_path, ttl_s=3600))

    assert provider.complete_json("sys", "user") is None
    assert inner.calls == 0


def test_corrupted_cache_file_is_treated_as_miss(tmp_path):
    cache = ResponseCache(tmp_path, ttl_s=3600)
    key = ResponseCache.make_key("p", "m", "s", "u")
    cache.set(key, {"ok": True})

    broken = next(tmp_path.rglob("*.json"))
    broken.write_text("{это не json", encoding="utf-8")

    assert cache.get(key) is None
