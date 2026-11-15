# Локальный контракт CLI и MCP

Команды выполняются из корня репозитория (Python 3.11+, только стандартная библиотека; в Windows можно использовать `py -3`). Кроме CLI, все исследовательские инструменты доступны как MCP-сервер `python3 -m ledger mcp` (конфигурации в `.mcp.json` и `.cursor/mcp.json`).

## Основные команды CLI

```sh
# Жизненный цикл кейса и маршрутизация
python3 -m ledger init .cases/demo "Стоит ли расширять пилот?"
python3 -m ledger route .cases/demo --budget balanced
python3 -m ledger source .cases/demo brief document input/brief.md
python3 -m ledger source .cases/demo pilot synthetic input/pilot.csv
python3 -m ledger source .cases/demo interview_1 interview input/store_owner.md
python3 -m ledger source .cases/demo checkout_ui ux input/pos_screen.md
python3 -m ledger source .cases/demo vendor web input/vendor.md --url https://example.org/page
python3 -m ledger status .cases/demo
python3 -m ledger checkpoint .cases/demo research "Сравнить сегменты и экономику"

# Детерминированная аналитика (zero-LLM тир)
python3 -m ledger rates .cases/demo/input/pilot.csv --group group --segment cohort \
  --denominator drills --numerators network_recovered checkout_completed false_success \
  --ci --guardrail "checkout_completed>=0.90" --guardrail "false_success<=0.03"
python3 -m ledger economics .cases/demo/input/economics.json

# Черновик, аудит обещаний, дашборд и утверждение человеком
python3 -m ledger propose .cases/demo proposal.json --strict-claims
python3 -m ledger audit-claims .cases/demo
python3 -m ledger dashboard .cases/demo
python3 -m ledger view .cases/demo --draft
python3 -m ledger accept .cases/demo managed_failover --expect <sha-черновика> \
  --by "Руководитель продукта" --reason "Проходит пороги для direct_pos"

# Пересмотр, обязательства, база знаний и телеметрия
python3 -m ledger view .cases/demo --decision 1
python3 -m ledger retire .cases/demo pilot --reason "Выгрузка отозвана"
python3 -m ledger evidence .cases/demo <sha>
python3 -m ledger done .cases/demo verify --decision 1 --note "Протокол проверки готов"
python3 -m ledger kb ingest .cases/demo
python3 -m ledger kb search "касса шлюз"
python3 -m ledger telemetry .cases/demo --step research --model gpt-5 --seconds 42 --input-tokens 12000 --output-tokens 950
python3 -m ledger audit-repo .
```

## Типы источников и эпистемические правила

- Поддерживаемые типы источников в `source`: `document`, `table`, `web`, `synthetic`, `interview`, `ux` (снимок до 8 МиБ в SQLite с SHA-256).
- Источники `synthetic` поддерживают наблюдения внутри модельного сценария, но никогда не могут обосновывать `fact` или служить единственным доказательством рыночного спроса.
- В базе знаний (`kb`) записи разделены на `primary_evidence` (требует `source_sha`, `can_cite_as_fact: true`), `synthetic_probe` (`can_cite_as_fact: false`) и `prior_conclusion` (`can_cite_as_fact: false` — прошлый вывод агента не превращается в факт). Устаревшие записи отзываются через `kb retract <id> --reason "..."`.

## Схема черновика `proposal.json`

| Поле | Структура |
| --- | --- |
| `title`, `summary` | Непустые строки. Краткое резюме для руководителя, включая ключевую развилку. |
| `reviewed` | Словарь всех активных `source_id → {sha, note}`. |
| `findings` | Словарь `finding_id → {kind, text, citations?, depends_on?}`. `kind` из `fact, observation, inference, hypothesis, forecast, unknown`. Цитата `{source, locator}`. |
| `options` | Словарь `option_id → {title, type, case, objection, depends_on}`. Обязательны все три типа `keep, change, close`. |
| `recommended` | ID рекомендованного варианта из `options`. |
| `strategy` | Ровно три блока `product, communication, experiment`, в каждом `{text, depends_on}`. В `communication` указывай проверяемые условия обещания, в `experiment` — защитные пороги и стоп-правило. |
| `actions` | Список `{id, owner, due, task, trigger}` с датой `YYYY-MM-DD`. |
