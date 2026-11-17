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

Что реально запускалось: старт и замер учебного кейса `retail-backup` — в **Codex CLI (`0.166.0`)**; после исчерпания лимита Codex разработка и трёхшаговый прогон открытого кейса МегаФона продолжены в **Antigravity** (субагентами одной сессии). Протокол `stdio MCP` проверен вызовом `python3 -m ledger mcp` из терминала ([02-mcp-portability-check.json](../demo/antifraud-subscription/02-mcp-portability-check.json)) и автотестом `test_mcp_server_handles_ndjson_and_framed_requests`. **В Cursor и Claude Code прогона пока не было**: конфиги `.cursor/` и `.mcp.json`/`CLAUDE.md` лежат в репозитории, проверка в этих IDE — по [docs/live-run.md](live-run.md), раздел 5.

| Среда | Статус проверки |
| --- | --- |
| Codex CLI `0.166.0` | запускался (учебный кейс `retail-backup`, сырые счётчики в [measurements.json](demo/measurements.json)) |
| Antigravity | запускался (3 шага открытого кейса субагентами одной сессии; [measured-telemetry.json](../demo/antifraud-subscription/runs/2026-11-17-antigravity-subagents/measured-telemetry.json)) |
| Cursor | конфиги есть, прогона не было — по [live-run.md](live-run.md) |
| Claude Code | конфиги есть, прогона не было — по [live-run.md](live-run.md) |

| Среда | Что подхватывается автоматически из корня | Канал вызова `ledger` | Практические различия при переносе `.cases/<кейс>` |
| --- | --- | --- | --- |
| **Codex CLI** | `AGENTS.md`, навыки `.agents/skills/*/SKILL.md` | Терминальный CLI (`python3 -m ledger ...`) | Высокий hit префиксного кэша (`~83,8%`) внутри серии вызовов; при исчерпании квоты аккаунта срабатывает сценарий `ledger route --failed-tool model_call` |
| **Antigravity** | `AGENTS.md`, навыки `.agents/skills/*/SKILL.md`, субагенты (`invoke_subagent`) | Терминальный CLI + мультимодальное чтение `.png` лендингов | Субагенты получают отдельный контекст без истории родительского чата (так собрано демо), но команды им формулирует оркестратор той же сессии — это не равно новым пользовательским чатам |
| **Cursor** | `.cursor/rules/decision-ledger.mdc` (`alwaysApply: true`), `.cursor/mcp.json` | Нативные MCP-тулы `ledger_status`, `ledger_route`, `ledger_rates`, `ledger_economics`, `ledger_audit_claims`, `ledger_kb_*`, `ledger_view`, `ledger_cost` | Ожидаемо: инструменты видны модели как нативные функции без shell-команд. **В Cursor не запускалось** — проверка по [live-run.md](live-run.md) |
| **Claude Code** | `CLAUDE.md`, `.mcp.json`, субагенты `agents/*.md`, хуки `.claude/settings.json` | Нативный MCP + CLI + проектные хуки | Ожидаемо: декларативные субагенты из `agents/` с фронтматтером `name / description`. **В Claude Code не запускалось** — проверка по [live-run.md](live-run.md) |

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
