from __future__ import annotations

import os

# English is the default because most people who find the Action on the
# Marketplace read English. Russian stays available for existing users.
# CONFIDENCE_LANG (also set by `--lang`) beats `language:` in confidence.yml,
# so one run can be switched without editing the repository's config.

SUPPORTED_LANGUAGES = ("en", "ru")
ENV_VAR = "CONFIDENCE_LANG"

_config_language = "en"


def _normalize(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    return value if value in SUPPORTED_LANGUAGES else None


def set_config_language(language: str | None) -> None:
    global _config_language
    _config_language = _normalize(language) or "en"


def current_language() -> str:
    return _normalize(os.environ.get(ENV_VAR)) or _config_language


def tr(en: str, ru: str) -> str:
    return ru if current_language() == "ru" else en
