import pytest

from confidence_scorer.extractors.js_extractor import diff_functions, extract_functions, node_available


def test_missing_node_binary_degrades_gracefully():
    bogus = "definitely-not-a-real-node-binary-xyz"
    assert extract_functions("function f() { return 1; }", node_binary=bogus) == {}
    assert diff_functions("function f() {}", "function f() { return 1; }", node_binary=bogus) == []


requires_node = pytest.mark.skipif(not node_available(), reason="Node.js / js_helpers node_modules недоступны")


@requires_node
def test_extract_and_diff_js_functions():
    old = "function isPrime(n) {\n  if (n < 2) return false;\n  return true;\n}\n"
    new = "function isPrime(n) {\n  if (n <= 2) return false;\n  return true;\n}\n"
    changes = {c.name: c for c in diff_functions(old, new)}
    assert changes["isPrime"].change_type == "modified"


@requires_node
def test_ts_types_are_extracted():
    old = "function add(a: number, b: number): number {\n  return a + b;\n}\n"
    new = "function add(a: number, b: number): number {\n  return a + b + 1;\n}\n"
    changes = {c.name: c for c in diff_functions(old, new)}
    params = changes["add"].new.params
    assert params[0].type_annotation == "number"
