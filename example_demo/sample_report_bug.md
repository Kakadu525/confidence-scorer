<!-- confidence-scorer:report -->
## 🔴 Confidence Score: 0/100

**Найден подтверждённый контрпример поведения: не мержить без ручной проверки**

> Покрытие проверками: **40%**, потолок score ограничен **70**.

| Проверка | Sub-score | Вес |
|---|---|---|
| Property-based tests (differential) | 0 | 100% |
| Semantic diff (AI) | - | - |
| Второй AI-ревьюер | - | - |

<details><summary>Property-based tests: 0 passed / 1 failed / 0 error / 0 skipped</summary>

| Функция | Файл | Статус | Детали |
|---|---|---|---|
| `apply_discount` | `pricing.py` | failed | вход=`{'price': '0.0', 'percent': '-0.5'}`, было=`raised ValueError('percent must be between 0 and 100')`, стало=`0.0` |

</details>

### Подтверждённые контрпримеры поведения
- pricing.py::apply_discount: старая и новая версия расходятся в том, бросают ли они исключение (вход={'price': '0.0', 'percent': '-0.5'}, было=raised ValueError('percent must be between 0 and 100'), стало=0.0)

<details><summary>Примечания</summary>

- semantic_diff: не выполнялся (AI-провайдер недоступен: не задана переменная окружения ANTHROPIC_API_KEY (provider: anthropic, model: claude-opus-5))
- second_reviewer: не выполнялся (AI-провайдер недоступен: не задана переменная окружения OPENAI_API_KEY (provider: openai, model: gpt-4.1))

</details>


<sub>Сгенерировано [confidence-scorer](https://github.com/Kakadu525/confidence-scorer). Доверяйте, но проверяйте.</sub>
