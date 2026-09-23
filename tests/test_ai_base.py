from confidence_scorer.ai.base import extract_json


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced_markdown():
    text = 'Вот результат:\n```json\n{"a": 1, "b": [1,2]}\n```\nСпасибо.'
    assert extract_json(text) == {"a": 1, "b": [1, 2]}


def test_extract_json_embedded_in_prose():
    text = 'Конечно! {"risk_score": 80, "changes": []}, вот моя оценка.'
    assert extract_json(text) == {"risk_score": 80, "changes": []}


def test_extract_json_returns_none_on_garbage():
    assert extract_json("это не json вообще") is None


def test_extract_json_empty_string():
    assert extract_json("") is None
