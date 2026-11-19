# Переносимость проекта между агентными IDE

Бриф задания требует доказать, что сохранённый проект переносится в другую среду разработки и продолжается с сохранённого места без потери оснований, истории версий и настроек.

---

## 1. Почему переносимость гарантирована архитектурно

1. **Единый самодостаточный контейнер кейса (`.cases/<кейс>/`)**:
   - `case.sqlite3` хранит побайтные снимки всех зарегистрированных источников (`SHA-256` в таблице `evidence`), текущий черновик с монотонным `draft_sequence`, все утверждённые ревизии, статусы поручений `actions` и `checkpoint`.
   - `cost.jsonl` хранит построчный журнал затрат, токенов, повторов и минут участия человека.
   - Ни один узел состояния не зависит от облачной памяти конкретного провайдера чата или проприетарного векторного стора IDE.
2. **Нулевые внешние зависимости (`Python 3.11+ stdlib`)**:
   - Чистый клон репозитория запускается в любой ОС (`Linux`, `macOS`, `Windows`) без `pip install`, `Docker` или внешних API-ключей.
3. **Двойной интерфейс инструментов (`CLI` + `stdio MCP`)**:
   - Среды с терминальным агентом (**Codex CLI**, **Antigravity**, **OpenCode**, **GigaCode**) вызывают `python3 -m ledger <cmd>`.
   - Среды с нативной шиной Model Context Protocol (**Cursor**, **Claude Code**, **Windsurf**) автоматически поднимают `python3 -m ledger mcp` из `.cursor/mcp.json` и `.mcp.json` (поддерживаются оба стандарта кадрирования: `NDJSON` и `Content-Length`).

---

## 2. Матрица совместимости и задокументированные различия сред

Что реально запускалось:
1. **Codex CLI (`0.166.0`, `gpt-6.1-sol`)** — старт и замер учебного кейса `retail-backup`.
2. **Antigravity** — трёхшаговый витринный прогон открытого кейса безопасности МегаФона и **полный двухсессионный прогон второго независимого открытого кейса вне антифрода** ([`demo/megakino-plus-2027/`](../demo/megakino-plus-2027/README.md): контентные мультиподписки «МегаКино» / «МегаФон Плюс», `376-ФЗ`, чтение `.xlsx` и шаблонный ввод `draft.yaml`, сырые логи в `demo/megakino-plus-2027/logs/`).
3. **Grok Build CLI (`v1.0.77`, `grok-4.7`)** — чистый 3-сессионный прогон открытого кейса безопасности от `18.11.2026` с новыми сеансами и `--resume` поверх `case.sqlite3` ([HONESTY.md](../demo/antifraud-subscription/runs/2026-11-18-live-grok-cli/HONESTY.md)).

Протокол `stdio MCP` (`12` инструментов чтения, `.xlsx`, расчёта, YAML-шаблона, аудита обещаний, БЗ, экспорта стратегий и выпуска паспорта) проверен вызовом `python3 -m ledger mcp` из терминала ([02-mcp-portability-check.json](../demo/antifraud-subscription/02-mcp-portability-check.json)) и автотестами (`test_mcp_server_handles_ndjson_and_framed_requests`, `test_mcp_exposes_xlsx_and_draft_template_tools`). **На рабочей Linux-машине графическое приложение Cursor IDE не установлено** (присутствует только CLI-шим `~/.local/bin/cursor`), поэтому запись экрана внутри графического окна Cursor и Claude Code не проводилась: конфиги `.cursor/` и `.mcp.json`/`CLAUDE.md` лежат в репозитории, регламент проверки — в [docs/live-run.md](live-run.md), раздел 5.

| Среда | Статус проверки |
| --- | --- |
| **Grok Build CLI `v1.0.77` (`grok-4.7`)** | запускался (3 независимых сеанса открытого кейса с записью `asciinema`, сырые логи в [runs/2026-11-18-live-grok-cli/](../demo/antifraud-subscription/runs/2026-11-18-live-grok-cli/)) |
| **Antigravity** | запускался (3 шага открытого кейса субагентами одной сессии; [measured-telemetry.json](../demo/antifraud-subscription/runs/2026-11-17-antigravity-subagents/measured-telemetry.json)) |
| **Codex CLI `0.166.0`** | запускался (учебный кейс `retail-backup`, сырые счётчики в [measurements.json](demo/measurements.json)) |
| **Cursor** | конфиги есть, MCP проверен из терминала; ручного запуска в GUI не было ([live-run.md](live-run.md)) |
| **Claude Code** | конфиги и субагенты есть, MCP проверен из терминала; ручного запуска не было ([live-run.md](live-run.md)) |

| Среда | Что подхватывается автоматически из корня | Канал вызова `ledger` | Практические различия при переносе `.cases/<кейс>` |
| --- | --- | --- | --- |
| **Grok Build CLI** | `AGENTS.md`, навыки `.agents/skills/*/SKILL.md`, субагенты | Терминальный CLI (`python3 -m ledger ...`) | Полный перенос состояния между новыми сеансами через `case.sqlite3` (Чат №2 восстановил контекст и выпустил Решение №1 за `144 с`) |
| **Codex CLI** | `AGENTS.md`, навыки `.agents/skills/*/SKILL.md` | Терминальный CLI (`python3 -m ledger ...`) | Высокий hit префиксного кэша (`~83,8%`) внутри серии вызовов; при исчерпании квоты аккаунта срабатывает сценарий `ledger route --failed-tool model_call` |
| **Antigravity** | `AGENTS.md`, навыки `.agents/skills/*/SKILL.md`, субагенты (`invoke_subagent`) | Терминальный CLI + мультимодальное чтение `.png` лендингов | Субагенты получают отдельный контекст без истории родительского чата (так собрана витрина стратегий), но команды им формулирует оркестратор той же сессии |
| **Cursor** | `.cursor/rules/decision-ledger.mdc` (`alwaysApply: true`), `.cursor/mcp.json` | 9 нативных MCP-тулов чтения/расчёта/БЗ + CLI для транзакционных мутаций (`init`, `source`, `propose`, `accept`) | Чтение статуса, экономика, интервалы Вильсона и аудит обещаний идут через `stdio MCP`, а явный гейт `accept --expect <sha>` вызывается через CLI. В GUI Cursor пока не запускалось ([live-run.md](live-run.md)) |
| **Claude Code** | `CLAUDE.md`, `.mcp.json`, субагенты `agents/*.md`, хуки `.claude/settings.json` | Нативный MCP (9 тулов) + CLI + проектные хуки | Декларативные субагенты из `agents/` с фронтматтером `name / description`. В Claude Code пока не запускалось ([live-run.md](live-run.md)) |

---

## 3. Воспроизводимая проверка переноса из чистого клона (за 60 секунд)

Любой проверяющий может убедиться, что кейс, созданный через CLI в одной среде, без изменений открывается и продолжает работу через MCP-протокол другой среды:

```sh
# 1. Распаковать чистый архив проекта в отдельную директорию
mkdir -p /tmp/dl-portability && unzip -q decision-ledger.zip -d /tmp/dl-portability
cd /tmp/dl-portability/decision-ledger

# 2. Среда А (терминальный агент Codex / Antigravity): создать кейс и сохранить чекпоинт
python3 -m ledger init .cases/port-check "Проверка переноса состояния между IDE"
cp examples/antifraud-subscription/brief.md .cases/port-check/brief.md
python3 -m ledger source .cases/port-check brief document brief.md
python3 -m ledger checkpoint .cases/port-check research "Продолжить исследование в другой IDE"

# 3. Среда Б (тот же stdio MCP JSON-RPC 2.0, которым пользуются Cursor / Claude Code): прочитать то же состояние
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ledger_status","arguments":{"case":".cases/port-check"}}}' \
  | python3 -m ledger mcp
```

Оба вызова обращаются к одному и тому же файлу `.cases/port-check/case.sqlite3`, возвращая идентичный `SHA-256` снимка источника и сохранённую контрольную точку `checkpoint`.
