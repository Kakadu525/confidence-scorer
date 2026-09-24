from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from confidence_scorer.i18n import tr


@dataclass
class ArgInfo:
    name: str
    annotation: str | None
    has_default: bool
    kind: str


@dataclass
class ExtractedFunction:
    qualname: str
    name: str
    lineno: int
    source: str
    docstring: str | None
    args: list[ArgInfo]
    returns: str | None
    is_async: bool
    decorators: list[str]
    is_public: bool


@dataclass
class ChangedFunction:
    qualname: str
    change_type: str
    old: ExtractedFunction | None
    new: ExtractedFunction | None


def _unparse_annotation(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001  # pragma: no cover
        return None


def _collect_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ArgInfo]:
    a = node.args
    result: list[ArgInfo] = []

    n_pos = len(a.posonlyargs) + len(a.args)
    n_defaults = len(a.defaults)
    default_offset = n_pos - n_defaults

    for idx, arg in enumerate([*a.posonlyargs, *a.args]):
        has_default = idx >= default_offset
        result.append(ArgInfo(arg.arg, _unparse_annotation(arg.annotation), has_default, "positional"))

    if a.vararg:
        result.append(ArgInfo(a.vararg.arg, _unparse_annotation(a.vararg.annotation), False, "vararg"))

    for kwarg, default in zip(a.kwonlyargs, a.kw_defaults, strict=True):
        result.append(
            ArgInfo(kwarg.arg, _unparse_annotation(kwarg.annotation), default is not None, "keyword_only")
        )

    if a.kwarg:
        result.append(ArgInfo(a.kwarg.arg, _unparse_annotation(a.kwarg.annotation), False, "kwarg"))

    return result


def _is_public(name: str) -> bool:
    if name.startswith("_"):
        return False
    return not (name.startswith("test_") or name.endswith("_test"))


def _normalize_source(src: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in src.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_functions(source: str) -> dict[str, ExtractedFunction]:
    functions, _ = extract_functions_checked(source)
    return functions


def extract_functions_checked(source: str) -> tuple[dict[str, ExtractedFunction], str | None]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {}, tr(f"syntax error: {exc.msg} (line {exc.lineno})", f"синтаксическая ошибка: {exc.msg} (строка {exc.lineno})")
    except ValueError as exc:
        return {}, tr(f"could not parse the file: {exc}", f"не удалось разобрать файл: {exc}")

    functions: dict[str, ExtractedFunction] = {}

    def visit(node: ast.AST, class_stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, [*class_stack, child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = ".".join([*class_stack, child.name])
                source_seg = ast.get_source_segment(source, child) or ""
                decorators = [_unparse_annotation(d) or "" for d in child.decorator_list]
                functions[qualname] = ExtractedFunction(
                    qualname=qualname,
                    name=child.name,
                    lineno=child.lineno,
                    source=source_seg,
                    docstring=ast.get_docstring(child),
                    args=_collect_args(child),
                    returns=_unparse_annotation(child.returns),
                    is_async=isinstance(child, ast.AsyncFunctionDef),
                    decorators=decorators,
                    is_public=_is_public(child.name),
                )

    visit(tree, [])
    return functions, None


def diff_functions(old_source: str | None, new_source: str | None) -> list[ChangedFunction]:
    changes, _ = diff_functions_checked(old_source, new_source)
    return changes


def diff_functions_checked(
    old_source: str | None, new_source: str | None
) -> tuple[list[ChangedFunction], str | None]:
    old_funcs, old_error = extract_functions_checked(old_source) if old_source else ({}, None)
    new_funcs, new_error = extract_functions_checked(new_source) if new_source else ({}, None)

    if new_error or old_error:
        if new_error:
            return [], tr(f"new version: {new_error}", f"новая версия: {new_error}")
        return [], tr(f"old version: {old_error}", f"старая версия: {old_error}")
    parse_error = None

    changes: list[ChangedFunction] = []
    all_qualnames = set(old_funcs) | set(new_funcs)

    for qualname in sorted(all_qualnames):
        old_fn = old_funcs.get(qualname)
        new_fn = new_funcs.get(qualname)

        if old_fn is None:
            changes.append(ChangedFunction(qualname, "added", None, new_fn))
        elif new_fn is None:
            changes.append(ChangedFunction(qualname, "removed", old_fn, None))
        elif _normalize_source(old_fn.source) != _normalize_source(new_fn.source):
            changes.append(ChangedFunction(qualname, "modified", old_fn, new_fn))

    return changes, parse_error
