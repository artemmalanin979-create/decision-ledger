import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ledger.strategies import _write_minimal_stdlib_pdf

ROOT = Path(__file__).resolve().parents[1]


class LogCostAndStrategyExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.case = self.base / "case"

    def cli(self, *args, ok=True) -> str:
        res = subprocess.run(
            [sys.executable, "-m", "ledger", *map(str, args)],
            cwd=ROOT,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(res.returncode, 0 if ok else 2, res.stderr)
        return res.stdout if ok else res.stderr

    def test_manual_self_report_is_flagged_unverified_and_replaced_by_from_log_on_real_grok_logs(self):
        self.cli("init", self.case, "Проверка телеметрии из реальных логов")
        (self.case / "note.md").write_text("Открытая заметка для теста.\n", encoding="utf-8")
        src = json.loads(self.cli("source", self.case, "note", "document", "note.md"))

        # 1. Manual LLM self-report must be flagged as unverified
        manual_entry = json.loads(
            self.cli(
                "cost",
                self.case,
                "--step",
                "intake",
                "--model",
                "grok-4.7",
                "--input-tokens",
                "18000",
                "--output-tokens",
                "1400",
                "--seconds",
                "70",
            )
        )
        self.assertFalse(manual_entry["verified"])
        self.assertEqual(manual_entry["telemetry_origin"], "self_reported_unverified")
        self.assertIn("НЕ ВЕРИФИЦИРОВАНО", manual_entry["method"])

        unv_summary = json.loads(self.cli("cost", self.case))
        self.assertFalse(unv_summary["telemetry_verified"])
        self.assertEqual(unv_summary["unverified_self_reported_steps"], 1)

        prop = {
            "title": "Тестовое решение",
            "summary": "Сводка для проверки предупреждения о неверифицированном самоотчёте.",
            "reviewed": {"note": {"sha": src["sha"], "note": "Проверка"}},
            "findings": {
                "f1": {
                    "kind": "fact",
                    "text": "Факт из заметки.",
                    "citations": [{"source": "note", "locator": "line 1"}],
                }
            },
            "options": {
                "keep": {"type": "keep", "title": "Оставить", "case": "База", "objection": "Нет роста", "depends_on": ["f1"]},
                "change": {"type": "change", "title": "Обновить", "case": "Рост", "objection": "Затраты", "depends_on": ["f1"]},
                "close": {"type": "close", "title": "Закрыть", "case": "Экономия", "objection": "Потеря", "depends_on": ["f1"]},
            },
            "recommended": "change",
            "strategy": {
                "product": {"text": "Обновляем экран согласия.", "depends_on": ["f1"]},
                "communication": {"text": "Показываем цену при условии согласия, кроме архивных планов.", "depends_on": ["f1"]},
                "experiment": {"text": "Пилот 4 недели против контроля, стоп при жалобах > 0,3%.", "depends_on": ["f1"]},
            },
            "actions": [{"id": "a1", "owner": "PM", "due": "2026-12-01", "task": "Проверить", "trigger": "Сбой"}],
        }
        (self.case / "prop.json").write_text(json.dumps(prop, ensure_ascii=False), encoding="utf-8")
        self.cli("propose", self.case, "prop.json", "--strict-claims")
        draft_md_before = self.cli("view", self.case, "--draft")
        self.assertIn("НЕ ВЕРИФИЦИРОВАНО: самоотчёт модели", draft_md_before)

        # 2. Rebuild cost.jsonl from real Grok CLI logs of Chats 1-3
        grok_run_dir = ROOT / "demo/antifraud-subscription/runs/2026-11-18-live-grok-cli"
        rebuilt = json.loads(self.cli("cost", self.case, "--from-log", grok_run_dir))
        self.assertTrue(rebuilt["rebuilt"])
        self.assertEqual(rebuilt["imported_steps"], 6)
        self.assertEqual(rebuilt["removed_unverified_self_reports"], 1)

        summary = rebuilt["cost"]
        self.assertTrue(summary["telemetry_verified"])
        self.assertEqual(summary["verified_steps"], 6)
        self.assertEqual(summary["unverified_self_reported_steps"], 0)
        self.assertEqual(summary["uncached_input_tokens"], 1136028)
        self.assertEqual(summary["cached_tokens"], 12578176)
        self.assertEqual(summary["input_tokens"], 13714204)
        self.assertEqual(summary["output_tokens"], 224066)
        self.assertEqual(summary["reasoning_tokens"], 158394)
        self.assertEqual(summary["model_calls"], 144)
        self.assertEqual(summary["wall_seconds"], 4733.0)
        self.assertEqual(summary["retries"], 1)
        self.assertAlmostEqual(summary["api_cost_usd"], 9.91554, places=5)
        self.assertIsNone(summary["api_cost_rub"])
        self.assertIsNone(summary["human_minutes"])

        draft_md_after = self.cli("view", self.case, "--draft")
        self.assertIn("верифицировано из логов CLI", draft_md_after)
        self.assertIn("$9.9155", draft_md_after)

    def test_from_log_parses_antigravity_transcript_and_codex_jsonl(self):
        self.cli("init", self.case, "Мульти-рантайм парсер логов")
        logs_dir = self.base / "runtime_logs"
        logs_dir.mkdir()

        ag_lines = [
            json.dumps({
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "created_at": "2026-11-18T10:00:00Z",
            }),
            json.dumps({
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "created_at": "2026-11-18T10:01:30Z",
                "input_tokens": 12000,
                "cache_read_tokens": 48000,
                "output_tokens": 1500,
                "tool_calls": [{"name": "read_file"}, {"name": "run_command"}],
            }),
        ]
        ag_file = logs_dir / "01_antigravity_step.jsonl.gz"
        ag_file.write_bytes(gzip.compress(("\n".join(ag_lines) + "\n").encode("utf-8")))

        codex_lines = [
            json.dumps({"type": "thread.started", "thread_id": "thr-42"}),
            json.dumps({"type": "item.completed", "item": {"type": "command_execution"}}),
            json.dumps({
                "type": "turn.completed",
                "usage": {"input_tokens": 25000, "cached_input_tokens": 20000, "output_tokens": 900},
            }),
        ]
        codex_file = logs_dir / "02_codex_step.jsonl"
        codex_file.write_text("\n".join(codex_lines) + "\n", encoding="utf-8")

        res = json.loads(self.cli("cost", self.case, "--from-log", ag_file, codex_file))
        self.assertEqual(res["imported_steps"], 2)
        c = res["cost"]
        self.assertTrue(c["telemetry_verified"])
        self.assertEqual(c["input_tokens"], 60000 + 25000)
        self.assertEqual(c["cached_tokens"], 48000 + 20000)
        self.assertEqual(c["output_tokens"], 1500 + 900)
        self.assertEqual(c["wall_seconds"], 90.0)
        self.assertIsNone(c["api_cost_rub"])

    def test_export_strategies_generates_md_and_pdf_from_sqlite_evidence_and_mcp(self):
        self.cli("init", self.case, "Развитие облачного бэкапа документов МСБ на 2027 год")
        inp = self.case / "input"
        inp.mkdir()
        market = inp / "market.md"
        market.write_text("По открытым данным 34% микропредприятий теряли накладные при сбое ПК.\n", encoding="utf-8")
        econ_spec = {
            "min_margin_rate": 0.50,
            "plans": {
                "keep_basic": {
                    "title": "Текущий тариф 190 ₽",
                    "epistemic_basis": "public_price_plus_model_assumptions",
                    "monthly_revenue": 190.0,
                    "recurring_costs": {"infra": 70.0, "support": 35.0},
                    "upfront_costs": {"cac": 300.0},
                    "amortize_months": 6,
                },
                "team_vault": {
                    "title": "Командный сейф 490 ₽",
                    "epistemic_basis": "public_price_plus_model_assumptions",
                    "monthly_revenue": 490.0,
                    "recurring_costs": {"infra": 95.0, "support": 45.0},
                    "upfront_costs": {"cac": 450.0},
                    "amortize_months": 9,
                },
            },
        }
        econ_file = inp / "economics.json"
        econ_file.write_text(json.dumps(econ_spec, ensure_ascii=False), encoding="utf-8")

        s_mkt = json.loads(self.cli("source", self.case, "market", "document", "input/market.md"))
        s_eco = json.loads(self.cli("source", self.case, "econ", "document", "input/economics.json"))

        proposal = {
            "title": "Стратегия облачного сейфа документов МСБ 2027",
            "summary": "Переход на командный сейф 490 ₽ с автосверкой резервных копий.",
            "reviewed": {
                "market": {"sha": s_mkt["sha"], "note": "Обзор потерь документов"},
                "econ": {"sha": s_eco["sha"], "note": "Модель юнит-экономики"},
            },
            "findings": {
                "f_loss": {
                    "kind": "fact",
                    "text": "34% микропредприятий сталкивались с потерей первичных документов.",
                    "citations": [{"source": "market", "locator": "строка 1"}],
                },
                "i_econ": {
                    "kind": "inference",
                    "text": "Командный сейф 490 ₽ проходит ориентир маржи 50% (вклад 61,2%).",
                    "citations": [{"source": "econ", "locator": "plans[1]"}],
                    "depends_on": ["f_loss"],
                },
                "u_churn": {
                    "kind": "unknown",
                    "text": "Фактическое удержание 90 дней на новой цене пока не измерено.",
                },
            },
            "options": {
                "keep_basic": {
                    "type": "keep",
                    "title": "Оставить базовый тариф 190 ₽",
                    "case": "Не требует изменений биллинга.",
                    "objection": "Вклад ниже целевого порога 50%.",
                    "depends_on": ["i_econ"],
                },
                "team_vault": {
                    "type": "change",
                    "title": "Командный сейф 490 ₽ на 3 бухгалтеров",
                    "case": "Проходит порог маржи 50% и закрывает сценарий совместной работы.",
                    "objection": "Чувствительность к оттоку в первые 90 дней.",
                    "depends_on": ["f_loss", "i_econ"],
                },
                "close_tier": {
                    "type": "close",
                    "title": "Закрыть платный тариф",
                    "case": "Нулевые расходы на поддержку.",
                    "objection": "Отказ от рентабельного сегмента.",
                    "depends_on": ["u_churn"],
                },
            },
            "recommended": "team_vault",
            "strategy": {
                "product": {"text": "Фокус на командном сейфе до 3 ролей с журналом версий.", "depends_on": ["f_loss", "i_econ"]},
                "communication": {
                    "text": "Подчёркиваем версионность при условии включённого агента синхронизации, кроме локальных папок вне списка.",
                    "depends_on": ["f_loss", "u_churn"],
                },
                "experiment": {
                    "text": "Пилот Q1 против контрольной витрины; стоп при жалобах на списания > 0,3%.",
                    "depends_on": ["i_econ", "u_churn"],
                },
            },
            "methodology": {
                "jtbd": {
                    "payer_job": "Когда бухгалтерия сдаёт отчётность, директор хочет сохранность первички без ручных флешек.",
                    "user_job": "Когда бухгалтер меняет накладную, он хочет откатить файл в один клик.",
                    "forces": {
                        "push": "Потери накладных при поломке ПК",
                        "pull": "Автоматический журнал версий на 3 человек",
                        "anxiety": "Опасение сложной настройки",
                        "habit": "Пересылка архивов в мессенджере",
                    },
                    "depends_on": ["f_loss"],
                },
                "promise_matrix": [
                    {
                        "promise": "Сохраняем версии выбранных папок документов автоматически",
                        "capability": "Фоновый агент с журналом ревизий за 90 дней",
                        "proof_findings": ["f_loss"],
                        "boundary": "Работает при доступе к сети и не покрывает невыбранные диски",
                        "channel": "Экран подключения в личном кабинете",
                    }
                ],
            },
            "actions": [
                {
                    "id": "pilot_q1",
                    "owner": "PM B2B",
                    "due": "2027-02-01",
                    "task": "Запустить когортный пилот",
                    "trigger": "Retention 90d ниже порога окупаемости",
                }
            ],
        }
        (self.case / "proposal.json").write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
        staged = json.loads(self.cli("propose", self.case, "proposal.json", "--strict-claims"))
        self.cli("accept", self.case, "team_vault", "--expect", staged["sha"], "--by", "Комитет МСБ", "--reason", "Одобрено")

        # Delete economics.json on disk to prove export-strategies reads blobs straight from case.sqlite3!
        econ_file.unlink()

        out_dir = self.base / "exported"
        exported = json.loads(self.cli("export-strategies", self.case, "-o", out_dir))
        self.assertEqual(exported["status"], "decision_r1")
        self.assertTrue(exported["claims_passed"])

        prod_md = Path(exported["product_md"])
        comms_md = Path(exported["comms_md"])
        prod_pdf = Path(exported["product_pdf"])
        comms_pdf = Path(exported["comms_pdf"])

        for p in (prod_md, comms_md, prod_pdf, comms_pdf):
            self.assertTrue(p.is_file(), f"Missing exported file: {p}")
        self.assertTrue(prod_pdf.read_bytes().startswith(b"%PDF-1."))
        self.assertTrue(comms_pdf.read_bytes().startswith(b"%PDF-1."))

        prod_txt = prod_md.read_text(encoding="utf-8")
        comms_txt = comms_md.read_text(encoding="utf-8")
        self.assertIn("Резюме для продуктового комитета", prod_txt)
        self.assertIn("Командный сейф 490 ₽", prod_txt)
        self.assertIn("Пороги смены решения", prod_txt)  # Pulled from SQLite evidence blob!
        self.assertIn("Матрица «Обещание коммуникации ↔ Возможность продукта»", comms_txt)
        self.assertIn("ПРОЙДЕН", comms_txt)

        # Also test MCP tool ledger_export_strategies and stdlib PDF fallback
        proc = subprocess.Popen(
            [sys.executable, "-m", "ledger", "mcp"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            mcp_out_dir = self.base / "mcp_exported"
            req = {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "ledger_export_strategies",
                    "arguments": {
                        "case": str(self.case),
                        "out_dir": str(mcp_out_dir),
                        "prefix": "committee",
                        "include_pdf": False,
                    },
                },
            }
            proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
            proc.stdin.close()
            resp = json.loads(proc.stdout.readline().decode("utf-8"))
            self.assertFalse(resp["result"]["isError"])
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertTrue(Path(payload["product_md"]).name == "committee-product.md")
            self.assertEqual(proc.wait(timeout=5), 0)
        finally:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
            proc.kill()
            proc.wait()

        fallback_pdf = self.base / "fallback.pdf"
        _write_minimal_stdlib_pdf(prod_txt, fallback_pdf)
        self.assertTrue(fallback_pdf.read_bytes().startswith(b"%PDF-1.4"))


if __name__ == "__main__":
    unittest.main()
