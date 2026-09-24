from __future__ import annotations

from confidence_scorer.extractors.python_extractor import ExtractedFunction
from confidence_scorer.i18n import current_language

STRATEGY_SCHEMA_DOC = """\
Allowed "kind" values (only these, return nothing else):
- {"kind": "integers", "min_value"?: int, "max_value"?: int}
- {"kind": "floats", "min_value"?: number, "max_value"?: number}
- {"kind": "text", "max_size"?: int}
- {"kind": "booleans"}
- {"kind": "binary", "max_size"?: int}
- {"kind": "none"}
- {"kind": "sampled_from", "values": [literal, literal, ...]}
- {"kind": "lists", "elements": <spec>, "max_size"?: int}
- {"kind": "tuples", "elements": [<spec>, <spec>, ...]}
- {"kind": "dictionaries", "keys": <spec>, "values": <spec>, "max_size"?: int}
- {"kind": "one_of", "options": [<spec>, <spec>, ...]}
"""

_ANSWER_LANGUAGE = {"en": "English", "ru": "Russian"}


def _answer_language_rule() -> str:
    # Only free-text fields follow the output language: severities and JSON keys
    # are parsed by code and must stay exactly as specified.
    return (
        f"\n\nWrite the free-text fields (description, verdict, summary) in "
        f"{_ANSWER_LANGUAGE[current_language()]}. Keep JSON keys and severity values exactly as specified."
    )


def strategy_generation_prompt(fn: ExtractedFunction, missing_params: list[str]) -> tuple[str, str]:
    system = (
        "You help build random input generators (property-based testing) for a Python function "
        "without explicit type hints. Answer ONLY with a JSON object of the form "
        '{"param_name": <spec>, ...} for the listed parameters, with no explanations and no markdown. '
        "Use EXCLUSIVELY the schema below: it is interpreted by code, not executed as a program, "
        "so any field outside the schema is ignored and the parameter is skipped.\n\n"
        + STRATEGY_SCHEMA_DOC
    )
    user = (
        f"Function `{fn.qualname}`:\n```python\n{fn.source}\n```\n\n"
        f"Docstring: {fn.docstring or '(none)'}\n\n"
        f"Parameters without a clear type: {missing_params}. "
        "Propose a reasonable value generator for EACH of them, based on the parameter name, "
        "the function body and the docstring (for example: `items` -> list, `email` -> text, `count`/`n` -> integers). "
        "If in doubt, use the most general option (integers/text)."
    )
    return system, user


_FENCE_BY_LANGUAGE = {"python": "python", "javascript": "javascript"}


def semantic_diff_prompt(
    file_path: str,
    qualname: str,
    old_source: str,
    new_source: str,
    language: str = "python",
) -> tuple[str, str]:
    system = (
        "You perform a semantic diff: compare the OLD and the NEW version of one function and describe "
        "only the BEHAVIORAL changes (what changes for the calling code), not formatting "
        "or variable renames. Pay special attention to: boundary changes (< vs <=), changes to "
        "error/exception handling, changed default values, changed side effects, "
        "changed return types, silently widened except blocks, removed input validation.\n\n"
        'Answer ONLY with JSON: {"changes": [{"description": str, "severity": "low"|"medium"|"high", '
        '"category": str}], "risk_score": int}. '
        "risk_score is from 0 to 100, where 100 = the new version behaves equivalently to the old one (safe), "
        "0 = radically different behavior / a bug is likely. If there are no behavioral changes, "
        'return {"changes": [], "risk_score": 100}.'
        + _answer_language_rule()
    )
    fence = _FENCE_BY_LANGUAGE.get(language, "")
    user = (
        f"File: {file_path}, function `{qualname}` (language: {language}).\n\n"
        f"OLD version:\n```{fence}\n{old_source}\n```\n\n"
        f"NEW version:\n```{fence}\n{new_source}\n```"
    )
    return system, user


def second_reviewer_prompt(diff_text: str, files_summary: str) -> tuple[str, str]:
    system = (
        "You are the second, fully independent code reviewer in a pipeline that checks "
        "AI-generated changes (a diff) before merge. You have NOT seen who wrote this diff or how, "
        "and you must not trust that it has already been checked. Your job: be as skeptical as possible "
        "and find real problems: logic errors, incorrect handling of edge cases, "
        "broken error handling, security issues (injections, unsafe deserialization, "
        "leaked secrets), race conditions, backward-incompatible API changes, forgotten edge cases. "
        "Don't nitpick code style or formatting, that is not your job. "
        "If the diff looks correct and safe, say so, don't invent problems.\n\n"
        'Answer ONLY with JSON: {"confidence": int (0-100, how confidently this can be merged), '
        '"verdict": str (a short one-sentence conclusion), '
        '"issues": [{"severity": "low"|"medium"|"high", "file": str, "description": str}], '
        '"summary": str}'
        + _answer_language_rule()
    )
    user = f"Changed files:\n{files_summary}\n\nUnified diff:\n```diff\n{diff_text}\n```"
    return system, user
