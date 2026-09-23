from __future__ import annotations

import ast
from typing import Any

Spec = dict[str, Any]

ALLOWED_KINDS = {
    "integers",
    "floats",
    "text",
    "booleans",
    "binary",
    "none",
    "sampled_from",
    "lists",
    "tuples",
    "dictionaries",
    "one_of",
}

_SIMPLE_KIND_BY_NAME = {
    "int": "integers",
    "float": "floats",
    "str": "text",
    "bool": "booleans",
    "bytes": "binary",
}


def annotation_to_spec(annotation: str | None) -> Spec | None:
    if not annotation:
        return None
    try:
        expr = ast.parse(annotation, mode="eval").body
    except SyntaxError:
        return None
    return _node_to_spec(expr)


def _name_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _node_to_spec(node: ast.AST) -> Spec | None:
    if isinstance(node, ast.Constant) and node.value is None:
        return {"kind": "none"}

    if isinstance(node, (ast.Name, ast.Attribute)):
        name = _name_of(node)
        if name in _SIMPLE_KIND_BY_NAME:
            return {"kind": _SIMPLE_KIND_BY_NAME[name]}
        return None

    if isinstance(node, ast.Subscript):
        base_name = _name_of(node.value)
        sl = node.slice
        args = list(sl.elts) if isinstance(sl, ast.Tuple) else [sl]

        if base_name in ("list", "List"):
            inner = _node_to_spec(args[0]) if args else None
            return {"kind": "lists", "elements": inner} if inner else None

        if base_name in ("dict", "Dict"):
            if len(args) == 2:
                k, v = _node_to_spec(args[0]), _node_to_spec(args[1])
                if k and v:
                    return {"kind": "dictionaries", "keys": k, "values": v}
            return None

        if base_name in ("tuple", "Tuple"):
            specs = [_node_to_spec(a) for a in args]
            if specs and all(specs):
                return {"kind": "tuples", "elements": specs}
            return None

        if base_name == "Optional":
            inner = _node_to_spec(args[0]) if args else None
            return {"kind": "one_of", "options": [inner, {"kind": "none"}]} if inner else None

        if base_name == "Union":
            specs = [_node_to_spec(a) for a in args]
            if specs and all(specs):
                return {"kind": "one_of", "options": specs}
            return None

        if base_name == "Literal":
            values = []
            for a in args:
                if isinstance(a, ast.Constant):
                    values.append(a.value)
                else:
                    return None
            return {"kind": "sampled_from", "values": values}

        return None

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _node_to_spec(node.left), _node_to_spec(node.right)
        if left and right:
            return {"kind": "one_of", "options": [left, right]}
        return None

    return None


_INT_EDGE_CANDIDATES = [-1000, -100, -10, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 10, 42, 100, 1000]
_FLOAT_EDGE_CANDIDATES = [float(e) for e in _INT_EDGE_CANDIDATES] + [-0.5, -0.001, 0.001, 0.5]


def _with_int_edges(base_strategy, min_value: int | None, max_value: int | None):
    from hypothesis import strategies as st

    edges = [
        e
        for e in _INT_EDGE_CANDIDATES
        if (min_value is None or e >= min_value) and (max_value is None or e <= max_value)
    ]
    if not edges:
        return base_strategy
    return st.one_of(*(st.just(e) for e in edges), base_strategy)


_TEXT_INTERESTING_CHARS = " \t\n-_./\\:@#'\"0aA"


def _text_strategy(max_size: int):
    from hypothesis import strategies as st

    return st.one_of(
        st.text(alphabet=_TEXT_INTERESTING_CHARS, max_size=max_size),
        st.text(max_size=max_size),
    )


def spec_to_strategy(spec: Spec):
    from hypothesis import strategies as st

    kind = spec.get("kind")
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"strategy kind не в allowlist: {kind!r}")

    if kind == "integers":
        kwargs = {k: spec[k] for k in ("min_value", "max_value") if k in spec}
        if kwargs:
            return _with_int_edges(st.integers(**kwargs), kwargs.get("min_value"), kwargs.get("max_value"))
        base = st.one_of(st.integers(min_value=-1000, max_value=1000), st.integers())
        return _with_int_edges(base, None, None)
    if kind == "floats":
        kwargs = {k: spec[k] for k in ("min_value", "max_value") if k in spec}
        if kwargs:
            return st.floats(allow_nan=False, allow_infinity=False, **kwargs)
        return st.one_of(
            *(st.just(v) for v in _FLOAT_EDGE_CANDIDATES),
            st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
            st.floats(allow_nan=False, allow_infinity=False),
        )
    if kind == "text":
        return _text_strategy(spec.get("max_size", 50))
    if kind == "booleans":
        return st.booleans()
    if kind == "binary":
        return st.binary(max_size=spec.get("max_size", 50))
    if kind == "none":
        return st.none()
    if kind == "sampled_from":
        values = spec.get("values") or []
        if not values:
            return st.none()
        return st.sampled_from(values)
    if kind == "lists":
        inner = spec_to_strategy(spec["elements"])
        return st.lists(inner, max_size=spec.get("max_size", 8))
    if kind == "tuples":
        return st.tuples(*[spec_to_strategy(s) for s in spec["elements"]])
    if kind == "dictionaries":
        return st.dictionaries(
            spec_to_strategy(spec["keys"]), spec_to_strategy(spec["values"]), max_size=spec.get("max_size", 5)
        )
    if kind == "one_of":
        return st.one_of(*[spec_to_strategy(s) for s in spec["options"]])

    raise ValueError(f"strategy kind не реализован: {kind!r}")  # pragma: no cover


def neutral_value(spec: Spec) -> Any:
    kind = spec.get("kind")
    if kind == "integers":
        return 0
    if kind == "floats":
        return 0.0
    if kind == "text":
        return ""
    if kind == "booleans":
        return False
    if kind == "binary":
        return b""
    if kind == "none":
        return None
    if kind == "sampled_from":
        values = spec.get("values") or [None]
        return values[0]
    if kind == "lists":
        return []
    if kind == "tuples":
        return tuple(neutral_value(s) for s in spec.get("elements", []))
    if kind == "dictionaries":
        return {}
    if kind == "one_of":
        options = spec.get("options") or [{"kind": "none"}]
        return neutral_value(options[0])
    return None


def edge_values(spec: Spec) -> list[Any]:
    kind = spec.get("kind")
    if kind == "integers":
        lo, hi = spec.get("min_value"), spec.get("max_value")
        return [e for e in _INT_EDGE_CANDIDATES if (lo is None or e >= lo) and (hi is None or e <= hi)]
    if kind == "floats":
        lo, hi = spec.get("min_value"), spec.get("max_value")
        return [e for e in _FLOAT_EDGE_CANDIDATES if (lo is None or e >= lo) and (hi is None or e <= hi)]
    if kind == "booleans":
        return [True, False]
    if kind == "text":
        return ["", " ", "a", "a b", "  a  ", "\n", "0", "-"]
    if kind == "none":
        return [None]
    if kind == "sampled_from":
        return list(spec.get("values") or [])
    return []


def build_explicit_examples(param_specs: dict[str, Spec], max_examples: int = 40) -> list[dict[str, Any]]:
    if not param_specs:
        return []

    neutral = {name: neutral_value(spec) for name, spec in param_specs.items()}
    examples: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    for name, spec in param_specs.items():
        for value in edge_values(spec):
            candidate = dict(neutral)
            candidate[name] = value
            key = tuple(sorted((k, repr(v)) for k, v in candidate.items()))
            if key in seen:
                continue
            seen.add(key)
            examples.append(candidate)
            if len(examples) >= max_examples:
                return examples

    return examples
