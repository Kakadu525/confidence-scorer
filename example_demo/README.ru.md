# Демо

[English](README.md) | **Русский**

`setup_demo.sh` собирает временный git-репозиторий с двумя коммитами, которые
изображают правки от AI.

1. `bug-commit` (`pricing.py`). AI «упростил» валидацию в `apply_discount` и
   убрал проверку нижней границы (`percent < 0`). Дифф в одну строку похож на
   безобидный рефакторинг. Differential property-testing находит контрпример
   за доли секунды: при `percent = -0.5` старая версия бросала `ValueError`, а
   новая возвращает результат. Итог: 0/100, вердикт `hard_fail`, exit code 1,
   то есть CLI заблокирует мерж.

2. `safe-commit` (`textutils.py`). AI переписал `normalize_whitespace` через
   `re.sub`, поведение осталось прежним. Property-тест проверяет это на
   десятках случайных строк и подтверждает эквивалентность. Итог: 70/100,
   вердикт `warn`, exit code 0. Мерж не блокируется, но и «можно мержить» не
   выдаётся: без AI-ключей отработала одна проверка из трёх, и score упирается
   в потолок по покрытию (см. «Свёртка в score» в [README.ru.md](../README.ru.md)). С
   настроенными ключами те же изменения получают до 100.

Так выглядит второй сценарий, если все три проверки выполняет локальная
qwen2.5-coder:7b:

![Безопасный рефакторинг: score 91/100](../docs/images/run-safe.png)

Замечание semantic diff здесь ложное: `" ".join(text.split())` тоже убирал
пробелы по краям строки. Модели на 7B иногда выдумывают такие изменения, и
property-тест с оценкой 100 это перевешивает.

Готовые прогоны лежат в `sample_report_bug.md` и `sample_report_safe.md`. Они
сделаны без AI-ключей, поэтому в них только property-тесты. С ключами в
разбивке появятся строки `semantic_diff` и `second_reviewer`.

## Как воспроизвести

Все команды запускаются из корня репозитория `confidence-scorer`, в Git Bash на
Windows тоже:

```bash
pip install -e ".[all]"
cd confidence_scorer/js_helpers && npm install && cd ../..   # если нужны JS/TS-проверки

bash example_demo/setup_demo.sh /tmp/confidence-scorer-demo

cd /tmp/confidence-scorer-demo
confidence-score doctor                                   # что заработает с текущими ключами
confidence-score run --base bug-commit~1 --head bug-commit
confidence-score run --base safe-commit~1 --head safe-commit
```

Для semantic diff и второго ревьюера задайте ключи перед `run`. Как их
получить, написано в разделе «AI-ключи и модели» корневого README.

```bash
export ANTHROPIC_API_KEY=...          # PowerShell: $env:ANTHROPIC_API_KEY = "..."
export OPENAI_API_KEY=...             # PowerShell: $env:OPENAI_API_KEY = "..."
confidence-score run --base bug-commit~1 --head bug-commit
```
