PYTHON ?= python3

.PHONY: help check test audit showcase mcp install-hooks

help:
	@echo "Decision Ledger — доступные команды:"
	@echo "  make check          Запустить полный набор unit-тестов и аудит безопасности репозитория"
	@echo "  make test           Запустить 26 автотестов со строгой проверкой предупреждений"
	@echo "  make audit          Проверить репозиторий на секреты, лишние БД и целостность JSON"
	@echo "  make showcase       Пересобрать сквозную витрину demo/retail-continuity/"
	@echo "  make mcp            Запустить stdio MCP-сервер (JSON-RPC 2.0)"
	@echo "  make install-hooks  Установить локальный git pre-commit хук"

check: test audit

test:
	$(PYTHON) -W default -m unittest discover -s tests -v

audit:
	$(PYTHON) -m ledger audit-repo .

showcase:
	$(PYTHON) scripts/build_demo_showcase.py

mcp:
	$(PYTHON) -m ledger mcp

install-hooks:
	sh hooks/install.sh
