from __future__ import annotations

import re

from confidence_scorer.checks.strategy_builder import Spec


def ts_type_to_spec(type_str: str | None) -> Spec | None:
    if not type_str:
        return None
    t = type_str.strip()

    if t == "number":
        return {"kind": "floats"}
    if t == "string":
        return {"kind": "text"}
    if t == "boolean":
        return {"kind": "booleans"}
    if t in ("null", "undefined"):
        return {"kind": "none"}

    m = re.match(r"^(.+)\[\]$", t)
    if m:
        inner = ts_type_to_spec(m.group(1).strip())
        return {"kind": "lists", "elements": inner} if inner else None

    m = re.match(r"^Array<(.+)>$", t)
    if m:
        inner = ts_type_to_spec(m.group(1).strip())
        return {"kind": "lists", "elements": inner} if inner else None

    if "|" in t:
        parts = [p.strip() for p in t.split("|")]
        specs = [ts_type_to_spec(p) for p in parts]
        if all(specs):
            return {"kind": "one_of", "options": specs}
        return None

    m = re.match(r"^'([^']*)'$", t) or re.match(r'^"([^"]*)"$', t)
    if m:
        return {"kind": "sampled_from", "values": [m.group(1)]}
    if re.match(r"^-?\d+(\.\d+)?$", t):
        return {"kind": "sampled_from", "values": [float(t) if "." in t else int(t)]}

    return None
