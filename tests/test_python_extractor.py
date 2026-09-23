from confidence_scorer.extractors.python_extractor import diff_functions, extract_functions


def test_extract_top_level_and_method():
    src = """
def foo(a, b):
    return a + b

class C:
    def method(self, x):
        return x

    def _private(self):
        pass
"""
    funcs = extract_functions(src)
    assert set(funcs) == {"foo", "C.method", "C._private"}
    assert funcs["foo"].is_public
    assert funcs["C.method"].is_public
    assert not funcs["C._private"].is_public


def test_diff_detects_modified_added_removed():
    old = "def a():\n    return 1\n\ndef b():\n    return 2\n"
    new = "def a():\n    return 100\n\ndef c():\n    return 3\n"
    changes = {c.qualname: c for c in diff_functions(old, new)}
    assert changes["a"].change_type == "modified"
    assert changes["b"].change_type == "removed"
    assert changes["c"].change_type == "added"


def test_diff_ignores_whitespace_only_changes():
    old = "def a():\n    return 1\n"
    new = "def a():\n\n    return 1\n"
    assert diff_functions(old, new) == []


def test_diff_handles_syntax_error_gracefully():
    assert extract_functions("def a(:\n") == {}


def test_arg_annotations_extracted():
    src = "def f(n: int, items: list[str] = None) -> bool:\n    return True\n"
    funcs = extract_functions(src)
    args = funcs["f"].args
    assert args[0].name == "n" and args[0].annotation == "int" and not args[0].has_default
    assert args[1].name == "items" and args[1].has_default
    assert funcs["f"].returns == "bool"
