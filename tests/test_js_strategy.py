from __future__ import annotations

import json
import subprocess

import pytest

from confidence_scorer.checks.js_strategy import ts_type_to_spec
from confidence_scorer.extractors.js_extractor import JS_HELPERS_DIR, node_available

CASES = [
    ("number", {"kind": "floats"}),
    ("string", {"kind": "text"}),
    ("boolean", {"kind": "booleans"}),
    ("null", {"kind": "none"}),
    ("undefined", {"kind": "none"}),
    ("number[]", {"kind": "lists", "elements": {"kind": "floats"}}),
    ("Array<string>", {"kind": "lists", "elements": {"kind": "text"}}),
    ("string[][]", {"kind": "lists", "elements": {"kind": "lists", "elements": {"kind": "text"}}}),
    ("number | null", {"kind": "one_of", "options": [{"kind": "floats"}, {"kind": "none"}]}),
    ("'a'", {"kind": "sampled_from", "values": ["a"]}),
    ('"b"', {"kind": "sampled_from", "values": ["b"]}),
    ("42", {"kind": "sampled_from", "values": [42]}),
    ("-1.5", {"kind": "sampled_from", "values": [-1.5]}),
    ("  number  ", {"kind": "floats"}),
]

UNSUPPORTED = ["", None, "MyType", "Record<string, number>", "() => void", "number | MyType", "unknown"]


@pytest.mark.parametrize(("type_str", "expected"), CASES)
def test_supported_types(type_str, expected):
    assert ts_type_to_spec(type_str) == expected


@pytest.mark.parametrize("type_str", UNSUPPORTED)
def test_unsupported_types_yield_none(type_str):
    assert ts_type_to_spec(type_str) is None


@pytest.mark.skipif(not node_available("node"), reason="нужен Node.js")
def test_python_and_js_ports_agree():
    script = (
        "const {typeStringToSpec} = require('./spec_to_arbitrary');"
        "const input = JSON.parse(process.argv[1]);"
        "process.stdout.write(JSON.stringify(input.map(typeStringToSpec)));"
    )
    inputs = [case[0] for case in CASES] + [t for t in UNSUPPORTED if t is not None]

    proc = subprocess.run(
        ["node", "-e", script, json.dumps(inputs)],
        cwd=str(JS_HELPERS_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    js_specs = json.loads(proc.stdout)

    assert js_specs == [ts_type_to_spec(t) for t in inputs]
