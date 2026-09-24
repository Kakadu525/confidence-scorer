from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPreset:
    base_url: str
    api_key_env: str | None
    max_output_tokens: int | None
    docs_url: str
    context_window: int | None = None


PRESETS: dict[str, ProviderPreset] = {
    "ollama": ProviderPreset(
        base_url="http://localhost:11434",
        api_key_env=None,
        max_output_tokens=4096,
        docs_url="https://docs.ollama.com/api",
        context_window=32768,
    ),
    "deepseek": ProviderPreset(
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        max_output_tokens=None,
        docs_url="https://api-docs.deepseek.com/",
    ),
    # The Beijing region console needs base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    "qwen": ProviderPreset(
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        max_output_tokens=None,
        docs_url="https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope",
    ),
    "openrouter": ProviderPreset(
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        max_output_tokens=None,
        docs_url="https://openrouter.ai/docs/quickstart",
    ),
}

GENERIC_OPENAI_COMPATIBLE = "openai_compatible"
