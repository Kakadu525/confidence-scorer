from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from confidence_scorer.ai.base import (
    DEFAULT_MAX_TOKENS,
    FailureTracker,
    describe_api_error,
    extract_json,
    looks_truncated,
)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_CONTEXT_WINDOW = 32768

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def native_root(base_url: str | None) -> str:
    root = (base_url or DEFAULT_OLLAMA_URL).rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def opener_for(url: str) -> urllib.request.OpenerDirector:
    host = urllib.parse.urlparse(url).hostname or ""
    if host in _LOCAL_HOSTS:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        context_window: int | None = None,
        max_output_tokens: int | None = None,
        timeout: float = 600.0,
    ):
        self.model = model
        self.root = native_root(base_url)
        self.context_window = context_window or DEFAULT_CONTEXT_WINDOW
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self._failures = FailureTracker()

    def missing_requirement(self) -> str | None:
        return None

    @property
    def available(self) -> bool:
        return True

    def failure_reason(self) -> str | None:
        return self._failures.last()

    def _post_json(self, path: str, payload: dict) -> dict:
        url = f"{self.root}{path}"
        request = urllib.request.Request(  # noqa: S310
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener_for(url).open(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def complete_json(self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict | list | None:
        self._failures.clear()
        num_predict = min(max_tokens, self.max_output_tokens) if self.max_output_tokens else max_tokens
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            "options": {"num_ctx": self.context_window, "num_predict": num_predict, "temperature": 0, "seed": 0},
        }
        try:
            body = self._post_json("/api/chat", payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            if exc.code == 404:
                self._failures.record(f"модель {self.model} не скачана в Ollama: ollama pull {self.model}")
            else:
                self._failures.record(f"Ollama вернула HTTP {exc.code}: {detail}")
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            self._failures.record(
                f"Ollama недоступна ({self.root}): {reason}. Запущена ли она? (ollama serve)"
            )
            return None
        except Exception as exc:  # noqa: BLE001
            self._failures.record(describe_api_error(exc, model=self.model, endpoint=self.root))
            return None

        prompt_chars = len(system) + len(user)
        if looks_truncated(prompt_chars, body.get("prompt_eval_count")):
            self._failures.record(
                f"промпт не поместился в окно контекста ({self.context_window} токенов): Ollama отрезала "
                f"начало, и модель видела не весь код. Увеличьте context_window или уменьшите "
                f"limits.max_full_diff_bytes"
            )
            return None

        text = (body.get("message") or {}).get("content") or ""
        parsed = extract_json(text)
        if parsed is None:
            self._failures.record("ответ модели не содержит JSON")
        return parsed
