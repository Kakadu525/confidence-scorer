from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from confidence_scorer.ai.base import DEFAULT_MAX_TOKENS, AIProvider, failure_reason, unavailable_reason
from confidence_scorer.ai.prompts import semantic_diff_prompt
from confidence_scorer.checks.severity import apply_ceiling, severity_ceiling, worst_severity
from confidence_scorer.config import Config
from confidence_scorer.i18n import tr
from confidence_scorer.models import FunctionChange


@dataclass
class SemanticChangeResult:
    qualname: str
    file_path: str
    risk_score: int | None
    changes: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass
class SemanticDiffCheckResult:
    results: list[SemanticChangeResult] = field(default_factory=list)

    @property
    def evaluated(self) -> list[SemanticChangeResult]:
        return [r for r in self.results if r.risk_score is not None]

    @property
    def sub_score(self) -> float | None:
        scored = [apply_ceiling(r.risk_score, r.changes) for r in self.evaluated]
        if not scored:
            return None
        return sum(scored) / len(scored)

    @property
    def consistency_notes(self) -> list[str]:
        notes = []
        for r in self.evaluated:
            ceiling = severity_ceiling(r.changes)
            if ceiling is not None and r.risk_score > ceiling:
                notes.append(
                    f"semantic_diff: {r.file_path}::{r.qualname}: "
                    + tr(
                        f"risk_score {r.risk_score} lowered to {ceiling}: "
                        f"the model itself found a change with severity {worst_severity(r.changes)}",
                        f"risk_score {r.risk_score} понижен до {ceiling}: "
                        f"модель сама нашла изменение с severity {worst_severity(r.changes)}",
                    )
                )
        return notes

    @property
    def skip_reason(self) -> str:
        errors = [r.error for r in self.results if r.error]
        if errors:
            return errors[0]
        return tr("no changed functions", "нет изменённых функций")

    @property
    def notable_changes(self) -> list[tuple[str, dict]]:
        out = []
        for r in self.evaluated:
            for change in r.changes:
                if change.get("severity") in ("medium", "high"):
                    out.append((f"{r.file_path}::{r.qualname}", change))
        return out


def _truncate(src: str | None, max_bytes: int) -> str:
    src = src or ""
    encoded = src.encode("utf-8")
    if len(encoded) <= max_bytes:
        return src
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n# ... (truncated)"


def _evaluate_one(fc: FunctionChange, provider: AIProvider | None, config: Config) -> SemanticChangeResult:
    if provider is None or not provider.available:
        return SemanticChangeResult(fc.qualname, fc.file_path, None, error=unavailable_reason(provider))

    old_src = _truncate(fc.old_source, config.limits.max_diff_bytes_to_ai)
    new_src = _truncate(fc.new_source, config.limits.max_diff_bytes_to_ai)
    system, user = semantic_diff_prompt(fc.file_path, fc.qualname, old_src, new_src, fc.language)
    raw = provider.complete_json(system, user, max_tokens=DEFAULT_MAX_TOKENS)

    if not isinstance(raw, dict) or "risk_score" not in raw:
        reason = failure_reason(provider) or tr("the AI returned an invalid answer", "AI вернул некорректный ответ")
        return SemanticChangeResult(fc.qualname, fc.file_path, None, error=reason)

    try:
        risk = int(raw["risk_score"])
    except (TypeError, ValueError):
        return SemanticChangeResult(fc.qualname, fc.file_path, None, error=tr("risk_score is not a number", "risk_score не число"))
    risk = max(0, min(100, risk))

    changes = raw.get("changes")
    changes = changes if isinstance(changes, list) else []
    changes = [c for c in changes if isinstance(c, dict) and "description" in c]

    return SemanticChangeResult(fc.qualname, fc.file_path, risk, changes)


def run_semantic_diff(
    function_changes: list[FunctionChange],
    provider: AIProvider | None,
    config: Config,
) -> SemanticDiffCheckResult:
    modified = [fc for fc in function_changes if fc.change_type == "modified"][: config.limits.max_functions_per_run]
    if not modified:
        return SemanticDiffCheckResult()

    max_workers = min(config.limits.max_parallel_ai_calls, len(modified))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="confidence-scorer-semdiff") as pool:
        results = list(pool.map(lambda fc: _evaluate_one(fc, provider, config), modified))

    return SemanticDiffCheckResult(results=results)
