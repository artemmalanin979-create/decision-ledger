# Живой прогон 2026-11-18 · Grok Build CLI (`grok-4.7`) · сырые артефакты

Чистый headless-прогон протокола [`docs/live-run.md`](../../../../docs/live-run.md) на снимке репозитория `7ba0640`. Запись велась в терминале через asciinema. Подробности, инциденты и аудит — в [`HONESTY.md`](HONESTY.md).

| Папка / файл | Что внутри |
|---|---|
| `turns.jsonl` | время каждого хода по системным часам (UTC+3), сеанс, модель, код выхода |
| `prompts/` | сообщения «человека» дословно: промпты протокола, цитаты из `manager.md`, утверждения Артёма |
| `harness/` | скрипты записи, `chat*.sid` с ID сеансов, `audit.py` |
| `logs/*.stream.jsonl.gz` | полный streaming-json CLI: каждый вызов инструмента, текст, `end` с токенами и `total_cost_usd` (оценка CLI) |
| `casts/*.cast.gz` | записи терминала asciinema v2, воспроизведение: `zcat … > x.cast && asciinema play x.cast` |
| `subagents/` | история субагентов (в т.ч. скептик Чата №3, см. инцидент изоляции в HONESTY.md) |
| `status/` | `ledger status` после каждого чата и финальные ответы агента |
| `case/` | артефакты прогона: нетронутый `proposal.json` и `decision-v1-live.md`, а также детерминированно пересобранные из сырых логов и SQLite `cost.jsonl` (`ledger cost --from-log`), `decision-passport-live.html`, `decision-v2-live.md` и выгруженные стратегии `strategy-product.{md,pdf}` и `strategy-comms.{md,pdf}` (`ledger export-strategies`) |
| `aborted-chat2-typo/` | прерванный через ~10 с запуск Чата №2 с опечаткой в фамилии |
| `clean-copy-git-log.txt` | коммиты чистой копии: снимок, `manager.md` после вопросов, `later/` перед Чатом №3 |

Видео (mp4) в git-репозиторий не входят.
