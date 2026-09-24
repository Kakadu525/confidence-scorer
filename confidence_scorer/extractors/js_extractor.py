from __future__ import annotations

import functools
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from confidence_scorer.i18n import tr

JS_HELPERS_DIR = Path(__file__).resolve().parent.parent / "js_helpers"
EXTRACT_SCRIPT = JS_HELPERS_DIR / "extract.js"

_NODE_TIMEOUT_S = 60


@dataclass
class JsParam:
    name: str
    type_annotation: str | None
    kind: str


@dataclass
class ExtractedJsFunction:
    name: str
    params: list[JsParam]
    source: str
    executable_source: str
    is_async: bool
    is_public: bool


@dataclass
class ChangedJsFunction:
    name: str
    change_type: str
    old: ExtractedJsFunction | None
    new: ExtractedJsFunction | None


class NodeUnavailable(RuntimeError):
    pass


@functools.lru_cache(maxsize=8)
def node_available(node_binary: str = "node") -> bool:
    try:
        subprocess.run([node_binary, "--version"], capture_output=True, timeout=10, check=False)
        return (JS_HELPERS_DIR / "node_modules").is_dir()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _run_node(node_binary: str, payload: dict) -> tuple[dict | None, str | None]:
    try:
        proc = subprocess.run(
            [node_binary, str(EXTRACT_SCRIPT)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_NODE_TIMEOUT_S,
            cwd=str(JS_HELPERS_DIR),
        )
    except FileNotFoundError:
        return None, tr(f"Node.js not found ({node_binary})", f"Node.js не найден ({node_binary})")
    except subprocess.TimeoutExpired:
        return None, tr(f"Node parser timeout ({_NODE_TIMEOUT_S}s)", f"таймаут Node-парсера ({_NODE_TIMEOUT_S}s)")
    except OSError as exc:
        return None, tr(f"could not start Node: {exc!r}", f"не удалось запустить Node: {exc!r}")

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:300]
        return None, tr(
            f"the Node parser exited with code {proc.returncode}: {stderr}",
            f"Node-парсер завершился с кодом {proc.returncode}: {stderr}",
        )
    if not proc.stdout:
        return None, tr("the Node parser returned no output", "Node-парсер не вернул вывод")
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, tr("the Node parser returned non-JSON", "Node-парсер вернул не-JSON")
    if "error" in result:
        return None, str(result["error"])
    return result, None


def extract_functions(source: str, node_binary: str = "node") -> dict[str, ExtractedJsFunction]:
    payload, _ = _run_node(node_binary, {"source": source})
    if payload is None:
        return {}
    return {name: _parse_extracted(f) for name, f in payload.get("functions", {}).items()}


def _parse_extracted(f: dict) -> ExtractedJsFunction:
    return ExtractedJsFunction(
        name=f["name"],
        params=[JsParam(p["name"], p.get("typeAnnotation"), p.get("kind", "positional")) for p in f["params"]],
        source=f["source"],
        executable_source=f.get("executableSource") or f["source"],
        is_async=f["isAsync"],
        is_public=f["isPublic"],
    )


def _parse_changes(raw_changes: list[dict]) -> list[ChangedJsFunction]:
    changes: list[ChangedJsFunction] = []
    for c in raw_changes:
        old_fn = _parse_extracted(c["old"]) if c.get("old") else None
        new_fn = _parse_extracted(c["new"]) if c.get("new") else None
        changes.append(ChangedJsFunction(c["name"], c["changeType"], old_fn, new_fn))
    return changes


def diff_functions(
    old_source: str | None, new_source: str | None, node_binary: str = "node"
) -> list[ChangedJsFunction]:
    changes, _ = diff_functions_checked(old_source, new_source, node_binary)
    return changes


def diff_functions_checked(
    old_source: str | None, new_source: str | None, node_binary: str = "node"
) -> tuple[list[ChangedJsFunction], str | None]:
    payload, error = _run_node(node_binary, {"old": old_source, "new": new_source})
    if payload is None:
        return [], error
    return _parse_changes(payload.get("changes", [])), None


def diff_files(
    files: list[tuple[str, str | None, str | None]], node_binary: str = "node"
) -> tuple[dict[str, list[ChangedJsFunction]], dict[str, str]]:
    if not files:
        return {}, {}

    payload, error = _run_node(
        node_binary, {"files": [{"path": path, "old": old, "new": new} for path, old, new in files]}
    )
    if payload is None:
        return {}, {path: error or tr("the Node parser is unavailable", "Node-парсер недоступен") for path, _, _ in files}

    diffs: dict[str, list[ChangedJsFunction]] = {}
    issues: dict[str, str] = {}
    results = payload.get("results", {})
    for path, _, _ in files:
        entry = results.get(path)
        if entry is None:
            issues[path] = tr("the Node parser returned no result for the file", "Node-парсер не вернул результат по файлу")
        elif "error" in entry:
            issues[path] = str(entry["error"])
        else:
            diffs[path] = _parse_changes(entry.get("changes", []))
    return diffs, issues
