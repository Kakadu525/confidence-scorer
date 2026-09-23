from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from confidence_scorer.ai.base import sdk_installed
from confidence_scorer.config import Config, ProviderConfig

CHECK_LABELS = {
    "property_tests": "Property-тесты (Python)",
    "property_tests_js": "Property-тесты (JS/TS)",
    "semantic_diff": "Semantic diff (AI)",
    "second_reviewer": "Второй AI-ревьюер",
    "strategy_generation": "AI-генерация входных данных",
}

OllamaProbe = Callable[[str], "list[str] | None"]


@dataclass
class CheckStatus:
    key: str
    label: str
    ready: bool
    detail: str
    scored: bool


@dataclass
class Diagnosis:
    checks: list[CheckStatus]
    coverage: float
    score_cap: float | None


def probe_ollama(root: str, timeout: float = 3.0) -> list[str] | None:
    from confidence_scorer.ai.ollama_provider import opener_for

    url = f"{root}/api/tags"
    try:
        with opener_for(url).open(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return [m.get("name", "") for m in payload.get("models", [])]


def _model_present(model: str, available: list[str]) -> bool:
    candidates = {model} if ":" in model else {model, f"{model}:latest"}
    return any(name in candidates for name in available)


def _target(cfg: ProviderConfig) -> str:
    extra = f" (effort: {cfg.effort})" if cfg.effort else ""
    return f"{cfg.provider} / {cfg.model}{extra}"


def _ollama_status(key: str, cfg: ProviderConfig, scored: bool, probe: OllamaProbe) -> CheckStatus:
    from confidence_scorer.ai.ollama_provider import native_root

    label = CHECK_LABELS[key]
    root = native_root(cfg.endpoint)
    models = probe(root)
    if models is None:
        return CheckStatus(key, label, False, f"{_target(cfg)}: Ollama не отвечает на {root}, запустите ollama serve", scored)
    if not _model_present(cfg.model, models):
        return CheckStatus(key, label, False, f"{_target(cfg)}: модель не скачана, выполните ollama pull {cfg.model}", scored)
    return CheckStatus(
        key, label, True, f"{_target(cfg)}: локально, окно {cfg.context_tokens} токенов, ключ не нужен", scored
    )


def _provider_status(
    key: str, cfg: ProviderConfig, env: Mapping[str, str], scored: bool, probe: OllamaProbe
) -> CheckStatus:
    if cfg.provider == "ollama":
        return _ollama_status(key, cfg, scored, probe)

    label = CHECK_LABELS[key]
    target = _target(cfg)
    if cfg.key_env and not env.get(cfg.key_env):
        return CheckStatus(key, label, False, f"{target}: нет {cfg.key_env}", scored)
    if cfg.sdk_module and not sdk_installed(cfg.sdk_module):
        return CheckStatus(
            key,
            label,
            False,
            f'{target}: ключ задан, но не установлен пакет: pip install "confidence-scorer[{cfg.sdk_module}]"',
            scored,
        )
    return CheckStatus(key, label, True, target, scored)


def diagnose(
    config: Config,
    env: Mapping[str, str] | None = None,
    node_ok: bool | None = None,
    ollama_probe: OllamaProbe | None = None,
) -> Diagnosis:
    env = os.environ if env is None else env
    probe = ollama_probe or probe_ollama

    if node_ok is None:
        from confidence_scorer.extractors.js_extractor import node_available

        node_ok = node_available(config.js.node_binary)

    checks: list[CheckStatus] = []

    if config.execute_changed_code:
        checks.append(CheckStatus("property_tests", CHECK_LABELS["property_tests"], True, "локально, ключи не нужны", True))
    else:
        checks.append(
            CheckStatus(
                "property_tests", CHECK_LABELS["property_tests"], False, "выключено: execute_changed_code: false", True
            )
        )

    if not (config.execute_changed_code and config.js.enabled):
        checks.append(CheckStatus("property_tests_js", CHECK_LABELS["property_tests_js"], False, "выключено в конфиге", False))
    elif node_ok:
        checks.append(CheckStatus("property_tests_js", CHECK_LABELS["property_tests_js"], True, "Node.js найден", False))
    else:
        checks.append(
            CheckStatus(
                "property_tests_js",
                CHECK_LABELS["property_tests_js"],
                False,
                "нет Node.js или зависимостей: cd confidence_scorer/js_helpers && npm install",
                False,
            )
        )

    providers = config.providers
    checks.append(_provider_status("semantic_diff", providers.semantic_diff, env, scored=True, probe=probe))
    reviewers = providers.second_reviewer
    review_rows: list[CheckStatus] = []
    for member in reviewers:
        row = _provider_status("second_reviewer", member, env, scored=True, probe=probe)
        if len(reviewers) > 1:
            row.label = f"{CHECK_LABELS['second_reviewer']} · {member.display_label}"
        review_rows.append(row)
    checks.extend(review_rows)
    checks.append(
        _provider_status("strategy_generation", providers.strategy_generation, env, scored=False, probe=probe)
    )

    weights = {
        "property_tests": config.weights.property_tests,
        "semantic_diff": config.weights.semantic_diff,
        "second_reviewer": config.weights.second_reviewer,
    }
    total = sum(weights.values()) or 1.0
    review_total = sum(m.weight for m in reviewers)
    review_ready = sum(m.weight for m, row in zip(reviewers, review_rows, strict=True) if row.ready)
    completeness = {
        "property_tests": 1.0 if any(c.key == "property_tests" and c.ready for c in checks) else 0.0,
        "semantic_diff": 1.0 if any(c.key == "semantic_diff" and c.ready for c in checks) else 0.0,
        "second_reviewer": review_ready / review_total if review_total else 0.0,
    }
    coverage = sum(weights[k] * completeness[k] for k in weights) / total

    cap = None
    if config.evidence.enabled and coverage < 1.0:
        cap = config.evidence.min_cap + (100 - config.evidence.min_cap) * coverage

    return Diagnosis(checks=checks, coverage=coverage, score_cap=cap)
