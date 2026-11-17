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

Проект прошёл реальный перенос в ходе разработки и контрольных прогонов: старт и базовый замер выполнены в **Codex CLI (`0.166.0`)**, а при исчерпании лимита аккаунта Codex разработка и трёхсессионный прогон открытого кейса МегаФона продолжены в **Antigravity** и проверены по протоколу MCP для **Cursor / Claude Code**.

| Среда | Что подхватывается автоматически из корня | Канал вызова `ledger` | Практические различия при переносе `.cases/<кейс>` |
| --- | --- | --- | --- |
| **Codex CLI** | `AGENTS.md`, навыки `.agents/skills/*/SKILL.md` | Терминальный CLI (`python3 -m ledger ...`) | Высокий hit префиксного кэша (`~83,8%`) внутри серии вызовов; при исчерпании квоты аккаунта срабатывает сценарий `ledger route --failed-tool model_call` |
| **Antigravity** | `AGENTS.md`, навыки `.agents/skills/*/SKILL.md`, изолированные субагенты (`invoke_subagent`) | Терминальный CLI + мультимодальное чтение `.png` лендингов | Позволяет запускать каждый сеанс и ролевых субагентов (`evidence-extractor`, `synthetic-respondent`, `skeptic-reviewer`) в гарантированно отдельных контекстах без общей истории чата |
| **Cursor** | `.cursor/rules/decision-ledger.mdc` (`alwaysApply: true`), `.cursor/mcp.json` | Нативные MCP-тулы `ledger_status`, `ledger_route`, `ledger_rates`, `ledger_economics`, `ledger_audit_claims`, `ledger_kb_*`, `ledger_view`, `ledger_cost` | Инструменты видны модели в списке нативных функций без формирования shell-команд; исключает ошибки экранирования кавычек в терминале |
| **Claude Code** | `CLAUDE.md`, `.mcp.json`, субагенты `agents/*.md`, хуки `.claude/settings.json` | Нативный MCP + CLI + проектные хуки | Поддерживает декларативные описания ролевых субагентов из папки `agents/` с фронтматтером `name / description` |

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

# 3. Среда Б (Cursor / Claude Code по протоколу stdio MCP JSON-RPC 2.0): прочитать то же состояние
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ledger_status","arguments":{"case":".cases/port-check"}}}' \
  | python3 -m ledger mcp
```

Оба вызова обращаются к одному и тому же файлу `.cases/port-check/case.sqlite3`, возвращая идентичный `SHA-256` снимка источника и сохранённую контрольную точку `checkpoint`.
