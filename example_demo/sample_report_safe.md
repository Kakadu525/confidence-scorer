<!-- confidence-scorer:report -->
## 🟡 Confidence Score: 70/100

**Умеренная уверенность, рекомендуется ревью перед мержем**

> Покрытие проверками: **40%**, потолок score ограничен **70**.

| Проверка | Sub-score | Вес |
|---|---|---|
| Property-based tests (differential) | 100 | 100% |
| Semantic diff (AI) | - | - |
| Второй AI-ревьюер | - | - |

<details><summary>Property-based tests: 1 passed / 0 failed / 0 error / 0 skipped</summary>

| Функция | Файл | Статус | Детали |
|---|---|---|---|
| `normalize_whitespace` | `textutils.py` | passed |  |

</details>

<details><summary>Примечания</summary>

- semantic_diff: не выполнялся (AI-провайдер недоступен: не задана переменная окружения ANTHROPIC_API_KEY (provider: anthropic, model: claude-opus-5))
- second_reviewer: не выполнялся (AI-провайдер недоступен: не задана переменная окружения OPENAI_API_KEY (provider: openai, model: gpt-4.1))
- score ограничен 70 из-за неполного покрытия проверками (отработало 40% веса проверок). Это потолок доверия, а не оценка качества кода

</details>


<sub>Сгенерировано [confidence-scorer](https://github.com/Kakadu525/confidence-scorer). Доверяйте, но проверяйте.</sub>
