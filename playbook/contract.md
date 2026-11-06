# Локальный контракт

Команды выполняются из корня репозитория. В Windows можно подставить `py -3` вместо `python3`. `<кейс>` — каталог рабочей задачи, например `.cases/demo`. Сохраняй в нём материалы, черновик и контрольную точку. `case.sqlite3` переносится вместе с папкой после окончания команды. Не редактируй SQLite напрямую.

```sh
python3 -m ledger init .cases/demo "Стоит ли расширять пилот?"
python3 -m ledger source .cases/demo brief document input/brief.md
python3 -m ledger source .cases/demo pilot synthetic input/pilot.csv
python3 -m ledger source .cases/demo vendor web input/vendor.md --url https://example.org/page
python3 -m ledger status .cases/demo
python3 -m ledger checkpoint .cases/demo research "Сравнить сегменты"
python3 -m ledger propose .cases/demo recommendation.json
python3 -m ledger view .cases/demo --draft
python3 -m ledger accept .cases/demo pilot --by "Руководитель продукта" --reason "В пределах порогов"
python3 -m ledger view .cases/demo --decision 1
python3 -m ledger evidence .cases/demo <sha>
python3 -m ledger done .cases/demo verify --note "Протокол проверки готов"
```

Типы источников `document`, `table`, `web`, `synthetic`. Снимок — до 8 МиБ. `evidence` выводит архивные текстовые материалы UTF-8. Путь в `source` и `propose` задаётся относительно папки кейса. `rates` принимает обычный путь к CSV, столбец группировки, необязательный столбец сегмента, знаменатель и список числителей. Проценты получай как долю × 100.

## Черновик JSON

В `reviewed` запиши все зарегистрированные ID и текущий `sha` из `status`. В `note` укажи роль, охват или причину исключения. Связывай каждый факт и наблюдение с источником и точным местом. Вывод может опираться на другие утверждения. Отмечай зависимости максимально узко, иначе пересмотр затронет лишнее. Каждому варианту и каждой части стратегии нужна опора на утверждения.

| Поле | Структура |
| --- | --- |
| `title`, `summary` | Непустые строки. Краткое резюме для руководителя, включая развилку. |
| `reviewed` | Словарь `source_id → {sha, note}`. |
| `findings` | Словарь `finding_id → {kind, text, citations?, depends_on?}`. `kind` из `fact, observation, inference, hypothesis, forecast, unknown`. Цитата `{source, locator}`. `depends_on` — список ID утверждений. |
| `options` | Словарь `option_id → {title, type, case, objection, depends_on}`. Обязательны типы `keep, change, close`. Для разных изменений можно создать несколько `change`. |
| `recommended` | Один из ID вариантов. |
| `strategy` | Ровно `product, communication, experiment`. В каждом `{text, depends_on}`. Допустим Markdown в тексте. |
| `actions` | Список `{id, owner, due, task, trigger}`. Дата `YYYY-MM-DD`. |

Структурные проверки отсекают отсутствующие ссылки, циклы, неактуальные версии и факты на основе синтетики. Модель и руководитель оценивают достаточность, причинность и качество трактовки. Утверждения в отчёте являются выводами о зарегистрированном снимке и не становятся самостоятельными источниками для следующей задачи.
