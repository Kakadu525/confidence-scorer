import pytest

from confidence_scorer.checks.strategy_builder import annotation_to_spec, spec_to_strategy


@pytest.mark.parametrize(
    "annotation,expected_kind",
    [
        ("int", "integers"),
        ("float", "floats"),
        ("str", "text"),
        ("bool", "booleans"),
        ("bytes", "binary"),
    ],
)
def test_simple_annotations(annotation, expected_kind):
    spec = annotation_to_spec(annotation)
    assert spec == {"kind": expected_kind}


def test_optional_annotation():
    spec = annotation_to_spec("Optional[int]")
    assert spec["kind"] == "one_of"
    kinds = {o["kind"] for o in spec["options"]}
    assert kinds == {"integers", "none"}


def test_union_with_pipe():
    spec = annotation_to_spec("int | None")
    assert spec["kind"] == "one_of"


def test_list_and_dict():
    assert annotation_to_spec("list[int]") == {"kind": "lists", "elements": {"kind": "integers"}}
    assert annotation_to_spec("Dict[str, int]") == {
        "kind": "dictionaries",
        "keys": {"kind": "text"},
        "values": {"kind": "integers"},
    }


def test_literal():
    spec = annotation_to_spec("Literal['a', 'b', 3]")
    assert spec == {"kind": "sampled_from", "values": ["a", "b", 3]}


def test_unknown_or_missing_annotation_returns_none():
    assert annotation_to_spec(None) is None
    assert annotation_to_spec("SomeCustomType") is None
    assert annotation_to_spec("dict") is None


def test_spec_to_strategy_builds_real_strategy():
    from hypothesis import given, settings

    strat = spec_to_strategy({"kind": "integers", "min_value": 0, "max_value": 10})
    seen = []

    @settings(max_examples=20, database=None)
    @given(strat)
    def check(v):
        seen.append(v)
        assert 0 <= v <= 10

    check()
    assert seen


def test_spec_to_strategy_rejects_unknown_kind():
    with pytest.raises(ValueError):
        spec_to_strategy({"kind": "eval_arbitrary_code"})


def test_spec_to_strategy_nested_allowlist_only():
    with pytest.raises(ValueError):
        spec_to_strategy({"kind": "lists", "elements": {"kind": "os_system"}})
