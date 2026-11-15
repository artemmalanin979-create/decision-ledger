# Демонстрационный проект: резервный интернет с проверкой кассы для малой розницы

Сквозной пример работы агента **Decision Ledger** от размытого продуктового запроса до утверждённой стратегии и пересмотра решения в новом сеансе после поступления свежих данных. Все данные открытые или явно помеченные учебно-синтетические.

## 1. Задача руководителя продукта и входные материалы

- Записка команды малого бизнеса: [brief.md](../../examples/retail-backup/initial/brief.md)
- Уточнение рамок комитета и порогов допуска: [manager.md](../../examples/retail-backup/manager.md)
- Словарь стендовых испытаний и выгрузка первого среза: [data_dictionary.md](../../examples/retail-backup/initial/data_dictionary.md), [pilot.csv](../../examples/retail-backup/initial/pilot.csv)
- Проверенные открытые аналоги (Verizon, Digi, Square): [market.md](../../examples/retail-backup/initial/market.md)
- Юнит-экономика тарифов: [economics.json](../../examples/retail-backup/initial/economics.json)
- Макет экрана кассы и заблокированного промо-баннера + UX-разбор: [pos_ux_mockup.png](../../examples/retail-backup/initial/pos_ux_mockup.png), [ux_checkout_notes.md](../../examples/retail-backup/initial/ux_checkout_notes.md)
- Ролевой прогон модельного респондента (`[SYNTHETIC_PROBE]`): [synthetic_store_probe.md](../../examples/retail-backup/initial/synthetic_store_probe.md)
- Поздняя сверка журналов касс и чеков (поступает во втором сеансе): [recount.csv](../../examples/retail-backup/later/recount.csv)

## 2. Сеанс 1 — Исследование, защита от ловушки малой выборки и Решение №1

1. **Маршрутизация (`ledger route`)**: [00-route-intake.json](00-route-intake.json) — количественные таблицы и финансовая модель направлены в детерминированный `zero_llm` расчёт без траты токенов на арифметику.
2. **Когортный расчёт и 95% ДИ Вильсона (`ledger rates --ci --guardrail`)**: [01-rates-initial.json](01-rates-initial.json) — несмотря на `0/20` ложных успехов в `legacy_gateway` на первом срезе, верхняя граница 95% интервала Вильсона равна **16,11%** (при лимите `<= 3%`), поэтому агент помечает сегмент флагом `masked_segment_uncertainty` и не допускает его без сверки.
3. **Юнит-экономика и стресс-тест (`ledger economics`)**: [02-economics.json](02-economics.json) — маржинальный вклад настроенного резерва `1 290 ₽` (`51,81%`), потолок прямых затрат `1 369,50 ₽/мес`, запас прочности `169,50 ₽` (рост прямых затрат на `+15%` уже пробивает порог `45%`).
4. **Аудит обещаний (`ledger audit-claims`)**: черновик маркетингового баннера «Ни одной потерянной продажи при любом обрыве на любой кассе» автоматически заблокирован; сформировано проверяемое обещание с оговоркой питания, LTE и совместимости кассы.
5. **Утверждённое Решение №1 (продуктовая, коммуникационная стратегия и план проверки)**:
   - Читаемый отчёт Markdown: [03-decision-v1.md](03-decision-v1.md)
   - Интерактивный HTML-паспорт решения: [03-dashboard-v1.html](03-dashboard-v1.html)

## 3. Сеанс 2 (после перерыва) — Поступление `recount.csv` и транзитивный пересмотр

1. **Область пересмотра (`approved_impact`)**: [04-impact-after-recount.json](04-impact-after-recount.json) — замена `pilot.csv` на `recount.csv` сохраняет исходный снимок по SHA-256 и точечно помечает к перепроверке зависимые наблюдения, варианты и блоки стратегии, оставляя рыночные факты и расчёт юнит-экономики в силе.
2. **Паспорт в момент обнаружения устаревших оснований**: [04-dashboard-stale.html](04-dashboard-stale.html)
3. **Пересчёт сверки (`masked_segment_failure`)**: [05-rates-recount.json](05-rates-recount.json) — общий итог `managed_failover` (`94,23%` покупок и `2,69%` ложных успехов) всё ещё проходит пороги `90% / 3%`, скрывая провал `legacy_gateway` (`70,00%` покупок и `30,00%` ложных успехов при `100%` восстановления сети).
4. **Утверждённое Решение №2 (допуск `direct_pos`, блокировка и диагностика `legacy_gateway`)**:
   - Читаемый отчёт Markdown: [05-decision-v2.md](05-decision-v2.md)
   - Интерактивный HTML-паспорт Решения №2: [05-dashboard-v2.html](05-dashboard-v2.html)
5. **Индексация в базу знаний с эпистемической защитой (`ledger kb`)**: [06-kb-search-sample.json](06-kb-search-sample.json) — первичные материалы помечены `can_cite_as_fact: true`, синтетический прогон и прошлые выводы агента — `can_cite_as_fact: false` (Ревизия №1 автоматически получила статус `superseded` Ревизией №2).
