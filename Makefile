PYTHON ?= python3
TEST_COUNT := $(shell $(PYTHON) -c "import unittest; print(unittest.defaultTestLoader.discover('tests').countTestCases())" 2>/dev/null || echo "all")

.PHONY: help check test audit showcase mcp install-hooks

help:
	@echo "Decision Ledger — доступные команды:"
	@echo "  make check          Запустить полный набор unit-тестов ($(TEST_COUNT)) и аудит безопасности репозитория"
	@echo "  make test           Запустить $(TEST_COUNT) автотестов со строгой проверкой предупреждений"
	@echo "  make audit          Проверить репозиторий и всю историю git на секреты, ПДн, лишние БД и JSON"
	@echo "  make showcase       Пересобрать учебную витрину переносимости demo/retail-continuity/"
	@echo "  make mcp            Запустить stdio MCP-сервер (JSON-RPC 2.0)"
	@echo "  make install-hooks  Установить локальный git pre-commit хук"

check: test audit

test:
	$(PYTHON) -W error::ResourceWarning -m unittest discover -s tests -v

audit:
	$(PYTHON) -m ledger audit-repo . --history

showcase:
	$(PYTHON) scripts/build_demo_showcase.py

mcp:
	$(PYTHON) -m ledger mcp

install-hooks:
	sh hooks/install.sh
