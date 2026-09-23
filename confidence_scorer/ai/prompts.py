from __future__ import annotations

from confidence_scorer.extractors.python_extractor import ExtractedFunction

STRATEGY_SCHEMA_DOC = """\
Разрешённые "kind" (только они, ничего другого возвращать нельзя):
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


def strategy_generation_prompt(fn: ExtractedFunction, missing_params: list[str]) -> tuple[str, str]:
    system = (
        "Ты помогаешь построить генераторы случайных входных данных (property-based testing) "
        "для python-функции без явных type hints. Отвечай ТОЛЬКО JSON-объектом вида "
        '{"param_name": <spec>, ...} для перечисленных параметров, без пояснений и без markdown. '
        "Используй ИСКЛЮЧИТЕЛЬНО схему ниже: она интерпретируется кодом, а не выполняется как "
        "программа, поэтому любое поле вне схемы будет проигнорировано и параметр пропущен.\n\n"
        + STRATEGY_SCHEMA_DOC
    )
    user = (
        f"Функция `{fn.qualname}`:\n```python\n{fn.source}\n```\n\n"
        f"Docstring: {fn.docstring or '(нет)'}\n\n"
        f"Параметры без понятного типа: {missing_params}. "
        "Предложи для КАЖДОГО из них разумный генератор значений, ориентируясь на имя параметра, "
        "тело функции и docstring (например: `items` -> list, `email` -> text, `count`/`n` -> integers). "
        "Если сомневаешься, используй самый общий вариант (integers/text)."
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
        "Ты выполняешь semantic diff: сравниваешь СТАРУЮ и НОВУЮ версии одной функции и описываешь "
        "именно ПОВЕДЕНЧЕСКИЕ изменения (что изменится для вызывающего кода), а не форматирование "
        "или переименование переменных. Особое внимание: изменения границ (< vs <=), изменения "
        "обработки ошибок/исключений, изменения дефолтных значений, изменения побочных эффектов, "
        "изменения типов возврата, тихо расширенные except-блоки, удалённые проверки входных данных.\n\n"
        'Ответь ТОЛЬКО JSON: {"changes": [{"description": str, "severity": "low"|"medium"|"high", '
        '"category": str}], "risk_score": int}. '
        "risk_score от 0 до 100, где 100 = новая версия ведёт себя эквивалентно старой (безопасно), "
        "0 = поведение радикально другое / вероятен баг. Если изменений в поведении нет, "
        'верни {"changes": [], "risk_score": 100}.'
    )
    fence = _FENCE_BY_LANGUAGE.get(language, "")
    user = (
        f"Файл: {file_path}, функция `{qualname}` (язык: {language}).\n\n"
        f"СТАРАЯ версия:\n```{fence}\n{old_source}\n```\n\n"
        f"НОВАЯ версия:\n```{fence}\n{new_source}\n```"
    )
    return system, user


def second_reviewer_prompt(diff_text: str, files_summary: str) -> tuple[str, str]:
    system = (
        "Ты второй, полностью независимый ревьюер кода в конвейере, который проверяет "
        "AI-сгенерированные изменения (diff) перед мержем. Ты НЕ видел, кто и как писал этот diff, "
        "и не должен доверять тому, что он уже был проверен. Твоя задача: максимально скептично "
        "найти реальные проблемы: логические ошибки, некорректную обработку граничных случаев, "
        "повреждённую обработку ошибок, проблемы безопасности (инъекции, небезопасная десериализация, "
        "утечки секретов), гонки состояний, обратную несовместимость API, забытые edge cases. "
        "Не придирайся к стилю кода и форматированию, это не твоя задача. "
        "Если diff выглядит корректным и безопасным, так и скажи, не выдумывай проблем.\n\n"
        'Ответь ТОЛЬКО JSON: {"confidence": int (0-100, насколько уверенно можно мержить), '
        '"verdict": str (короткий вывод в одно предложение), '
        '"issues": [{"severity": "low"|"medium"|"high", "file": str, "description": str}], '
        '"summary": str}'
    )
    user = f"Изменённые файлы:\n{files_summary}\n\nUnified diff:\n```diff\n{diff_text}\n```"
    return system, user
