---
name: product-frameworks
description: Структурная проработка стратегии через JTBD (плательщик vs пользователь), Opportunity Solution Tree (Торрес), Pre-mortem (Клейн) и матрицу обещаний.
---

# Навык: Продуктовая методология (JTBD · OST · Pre-mortem · Матрица обещаний)

Применяй этот навык перед сборкой `proposal.json`, чтобы стратегия опиралась не на общие рассуждения, а на проверяемые продуктовые артефакты (подробные правила — в `playbook/jtbd.md`, `playbook/ost.md`, `playbook/premortem.md`, `playbook/promise-matrix.md`).

## Порядок работы

1. **JTBD (`methodology.jtbd`)**: раздели задачу плательщика (`payer_job`) и конечного пользователя (`user_job`), заполни 4 силы переключения (`push`, `pull`, `anxiety`, `habit`) со ссылками `depends_on` на узлы `findings`.
2. **Opportunity Solution Tree (`methodology.ost`)**: задай целевой `outcome`, выдели 3–4 ключевые возможности (`opportunities`) из подтверждённых болей и пробелов рынка, привяжи к ним альтернативы из `options` (включая `keep` и `close`) и укажи `assumption_test` для каждого активного решения.
3. **Pre-mortem (`methodology.premortem`)**: перенесись на 6 месяцев вперёд и опиши минимум 3 конкретных механизма провала выбранного варианта с оценкой `probability`, `impact`, `preventability` (`low` / `medium` / `high`) и мерой защиты `mitigation`. Риски с `impact = high` перенеси в стоп-правила эксперимента.
4. **Матрица обещаний (`methodology.promise_matrix`)**: для каждого ключевого тезиса коммуникации свяжи `promise` -> `capability` -> `proof_findings` -> `boundary` -> `channel` и проверь через `python3 -m ledger audit-claims <case>`.
