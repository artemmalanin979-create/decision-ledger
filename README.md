# Decision Ledger — Стратег Фабрики продуктов

ИИ-агент для руководителя цифрового продукта, который ведёт задачу от размытого запроса и разнородных источников до согласованной **продуктовой и коммуникационной стратегии** — и продолжает работу после принятия решения: хранит неизменяемые снимки оснований, контролирует обязательства команды и пересматривает только затронутые выводы, когда поступают новые данные.

Разработано для номинации **«ИИ-проект» («Стратег Фабрики продуктов» от МегаФона) MGIMO×AI Challenge 2026**.

## Быстрый старт

Требуется только **Python 3.11+** (стандартная библиотека, `0` внешних пакетов `pip`, без ключей в репозитории) и любая агентная IDE с доступом к файлам и терминалу: **Cursor, Codex, Claude Code, Windsurf, OpenCode, GigaCode или Antigravity**. Модель и веб-поиск предоставляются самой IDE; локальная аналитика, база знаний и MCP-сервер работают полностью офлайн.

1. Откройте корень репозитория в IDE:
   - **Codex / OpenCode / Antigravity** автоматически читают [AGENTS.md](AGENTS.md) и навыки в [.agents/skills/](.agents/skills/).
   - **Cursor** подхватывает правило [.cursor/rules/decision-ledger.mdc](.cursor/rules/decision-ledger.mdc) и MCP-сервер [.cursor/mcp.json](.cursor/mcp.json).
   - **Claude Code** читает [CLAUDE.md](CLAUDE.md), MCP-конфиг [.mcp.json](.mcp.json) и проектные хуки [.claude/settings.json](.claude/settings.json).
2. Поставьте задачу руководителем продукта (пример на учебном кейсе):
   > Помоги подготовить решение для комитета о платном резервном интернете для небольших магазинов. Используй материалы из `examples/retail-backup/initial`, сохрани работу в `.cases/retail`. Сначала задай только те вопросы, которые меняют рекомендацию.
3. Передайте уточнения комитета ([manager.md](examples/retail-backup/manager.md)) — агент маршрутизирует расчёт долей, 95% интервалов Вильсона и юнит-экономики в детерминированный CLI/MCP, проверит обещания коммуникации через `audit-claims`, сгенерирует интерактивный HTML-паспорт `dashboard.html` и остановится на согласовании.
4. Откройте **новый чат** и передайте позднюю сверку ([recount.csv](examples/retail-backup/later/recount.csv)) — агент восстановит состояние из `.cases/retail`, покажет транзитивный граф затронутых выводов (`approved_impact`) и подготовит пересмотренное Решение №2, сохранив Решение №1 в истории.

Готовый сквозной пример для жюри (задача, исходные данные, обе стратегии и интерактивные HTML-дашборды) собран в разделе **[demo/retail-continuity/](demo/retail-continuity/README.md)**.

### Проверка без модели (из чистого клона)

```sh
python3 -m unittest discover -s tests -v
python3 -m ledger audit-repo .
python3 -m ledger rates examples/retail-backup/later/recount.csv \
  --group group --segment cohort --denominator drills \
  --numerators network_recovered checkout_completed false_success \
  --ci --guardrail checkout_completed>=0.90 --guardrail false_success<=0.03
python3 -m ledger economics examples/retail-backup/initial/economics.json
```

Для установки локального git-хука безопасности и тестов выполните `sh hooks/install.sh`.

## Архитектура и реализованные возможности

Каждый компонент решает конкретную проблему доверия, точности или стоимости контекста:

| Слой | Файлы | Польза для руководителя продукта |
| --- | --- | --- |
| **Инструкции и маршрутизация** | [AGENTS.md](AGENTS.md), [ledger/router.py](ledger/router.py) | Выбирает самый дешёвый достаточный шаг (`zero_llm` CLI для счёта, `fast` субагент для выжимки, `reasoning` для синтеза) и перестраивает маршрут при сбое инструмента без повтора выполненной работы |
| **Навыки (`Skills`)** | [.agents/skills/](.agents/skills/) | 5 модульных процедур (`problem-framing`, `cohort-and-economics`, `synthetic-interviews-ux`, `strategy-and-claims`, `decision-followup`), подгружаемых по этапу |
| **Субагенты (`Subagents`)** | [agents/](agents/) | `evidence-extractor` сжимает первичные документы вне главного контекста, `synthetic-respondent` проверяет оффер в роли клиента со строгой плашкой `[SYNTHETIC_PROBE]`, `skeptic-reviewer` ищет слабые места перед комитетом |
| **Реестр оснований и пересмотр** | [ledger/store.py](ledger/store.py), [ledger/review.py](ledger/review.py) | Транзакционный SQLite со снимками SHA-256, топологической проверкой графа `findings -> options/strategy`, защитой утверждения по SHA черновика (`--expect`) и транзитивным расчётом `impact` при смене данных |
| **Когортная статистика и экономика** | [ledger/stats.py](ledger/stats.py), [ledger/table.py](ledger/table.py), [ledger/economics.py](ledger/economics.py) | 95% доверительные интервалы Вильсона (ловят риск на малых выборках вроде `0/20`), авто-детектор парадокса Симпсона и маскировки провала сегмента средним, юнит-экономика со стресс-тестами `+15% / +25%` к затратам |
| **Аудит обещаний и форматов** | [ledger/claims.py](ledger/claims.py) | Проверяет соответствие коммуникации границам продукта (блокирует безусловные «100% / на любой кассе» и попытки доказать рыночный спрос синтетическими данными), поддерживает источники `document`, `table`, `web`, `synthetic`, `interview`, `ux` |
| **Поисковая база знаний (`KB`)** | [ledger/kb.py](ledger/kb.py) | SQLite FTS5 с поиском по русским словоформам, связями (`supports`, `contradicts`, `supersedes`), отзывом устаревших записей (`retract`) и защитой памяти: прошлые выводы агента (`prior_conclusion`) и синтетика помечены `can_cite_as_fact: false` |
| **Интеграция MCP + CLI + Hooks** | [ledger/mcp_server.py](ledger/mcp_server.py), [.mcp.json](.mcp.json), [hooks/](hooks/) | Полноценный stdio MCP-сервер (NDJSON и `Content-Length`) + CLI + `pre-commit` хук защиты от утечки секретов и битых артефактов |
| **Визуальный паспорт решения** | [ledger/dashboard.py](ledger/dashboard.py) | Генерирует самодостаточный интерактивный `dashboard.html` с кликабельной трассировкой от SHA источника до варианта комитета и обязательств команды |

## Ключевые команды

| Действие | Команда |
| --- | --- |
| Маршрут следующего шага / восстановление при сбое | `python3 -m ledger route .cases/retail` |
| Состояние кейса и область пересмотра | `python3 -m ledger status .cases/retail` |
| Интерактивный HTML-паспорт для руководителя | `python3 -m ledger dashboard .cases/retail` |
| Аудит обещаний коммуникации | `python3 -m ledger audit-claims .cases/retail` |
| Поиск в базе знаний с эпистемическим фильтром | `python3 -m ledger kb search "касса шлюз"` |
| Запуск MCP-сервера | `python3 -m ledger mcp` |

Сравнение с обычным чатом, телеметрия токенов и разбор ошибок задокументированы в **[docs/benchmark.md](docs/benchmark.md)**. Второй независимый кейс в **[examples/call-filter/](examples/call-filter/)** демонстрирует переносимость на другую предметную область и метрику.
