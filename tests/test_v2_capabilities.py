import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class V2CapabilitiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.case = self.base / "case"
        self.kb_dir = self.base / "kb"

    def cli(self, *args, ok=True):
        res = subprocess.run(
            [sys.executable, "-m", "ledger", *map(str, args)],
            cwd=ROOT, encoding="utf-8", capture_output=True, check=False,
        )
        self.assertEqual(res.returncode, 0 if ok else 2, res.stderr)
        return res.stdout if ok else res.stderr

    def test_kb_fts_russian_inflections_epistemic_guard_and_retraction(self):
        # 1. Primary evidence requires source_sha
        self.cli(
            "kb", "--kb-dir", self.kb_dir, "upsert", "doc.1", "primary_evidence",
            "--title", "Испытание кассы на шлюзе", "--body", "При отключении канала кассой зафиксирована задержка.",
            ok=False,
        )
        self.cli(
            "kb", "--kb-dir", self.kb_dir, "upsert", "doc.1", "primary_evidence",
            "--sha", "abc123", "--title", "Испытание кассы на шлюзе",
            "--body", "При отключении канала кассой зафиксирована задержка подтверждения.",
            "--tags", "pos", "gateway",
        )
        self.cli(
            "kb", "--kb-dir", self.kb_dir, "upsert", "synth.1", "synthetic_probe",
            "--title", "Модельный владелец пекарни",
            "--body", "Боится отпускать выпечку без бумажного чека при очереди.",
            "--tags", "interview",
        )
        self.cli(
            "kb", "--kb-dir", self.kb_dir, "upsert", "prior.1", "prior_conclusion",
            "--title", "Решение прошлого квартала",
            "--body", "Старый шлюз временно отложен из-за малой выборки касс.",
            "--tags", "decision",
        )

        # Inflected Russian query ("кассами шлюзов") should match "кассы / кассой / шлюзе"
        found = json.loads(self.cli("kb", "--kb-dir", self.kb_dir, "search", "кассами шлюзов"))
        ids = [item["id"] for item in found["results"]]
        self.assertIn("doc.1", ids)
        by_id = {item["id"]: item for item in found["results"]}
        self.assertTrue(by_id["doc.1"]["can_cite_as_fact"])

        all_items = json.loads(self.cli("kb", "--kb-dir", self.kb_dir, "search", ""))["results"]
        facts_map = {item["id"]: item["can_cite_as_fact"] for item in all_items}
        self.assertEqual(facts_map, {"doc.1": True, "synth.1": False, "prior.1": False})

        # Retract outdated prior conclusion -> hidden by default, preserved for audit
        self.cli(
            "kb", "--kb-dir", self.kb_dir, "retract", "prior.1",
            "--reason", "Получена финальная сверка по 20 прогонам",
        )
        active_after = json.loads(self.cli("kb", "--kb-dir", self.kb_dir, "search", "шлюз"))
        self.assertNotIn("prior.1", [r["id"] for r in active_after["results"]])
        with_inactive = json.loads(
            self.cli("kb", "--kb-dir", self.kb_dir, "search", "шлюз", "--include-inactive")
        )
        retracted = next(r for r in with_inactive["results"] if r["id"] == "prior.1")
        self.assertEqual(retracted["status"], "retracted")
        self.assertFalse(retracted["can_cite_as_fact"])

    def test_claim_auditor_catches_overclaims_and_synthetic_demand_proof(self):
        self.cli("init", self.case, "Запускать ли сервис?")
        (self.case / "sim.csv").write_text("arm,users,ok\nA,50,48\n", encoding="utf-8")
        src = json.loads(self.cli("source", self.case, "sim", "synthetic", "sim.csv"))

        bad_proposal = {
            "title": "Запуск",
            "summary": "Сводка",
            "reviewed": {"sim": {"sha": src["sha"], "note": "Синтетика"}},
            "findings": {
                "f1": {
                    "kind": "observation",
                    "text": "Синтетический прогон доказывает спрос на рынке.",
                    "citations": [{"source": "sim", "locator": "row 2"}],
                },
                "f2": {"kind": "unknown", "text": "Полевая конверсия неизвестна."},
            },
            "options": {
                "keep": {"title": "Оставить", "type": "keep", "case": "База", "objection": "Нет роста", "depends_on": ["f2"]},
                "go": {"title": "Запуск", "type": "change", "case": "Рост", "objection": "Риск", "depends_on": ["f1"]},
                "stop": {"title": "Закрыть", "type": "close", "case": "Фокус", "objection": "Потеря", "depends_on": ["f2"]},
            },
            "recommended": "go",
            "strategy": {
                "product": {"text": "Запускаем только для прямых касс.", "depends_on": ["f1"]},
                "communication": {"text": "Гарантируем продажи на 100% без сбоев на любой кассе!", "depends_on": ["f1"]},
                "experiment": {"text": "Посмотрим через месяц.", "depends_on": ["f2"]},
            },
            "actions": [{"id": "a1", "owner": "PM", "due": "2026-11-30", "task": "Проверить", "trigger": "Сбой"}],
        }
        (self.case / "bad.json").write_text(json.dumps(bad_proposal, ensure_ascii=False), encoding="utf-8")
        # Strict mode blocks overclaiming proposal
        self.cli("propose", self.case, "bad.json", "--strict-claims", ok=False)
        # Non-strict stages draft and reports warnings count, inspectable via audit-claims
        staged = json.loads(self.cli("propose", self.case, "bad.json"))
        self.assertGreaterEqual(staged["claim_warnings"], 3)
        audit = json.loads(self.cli("audit-claims", self.case))
        self.assertFalse(audit["passed"])
        codes = {w["code"] for w in audit["warnings"]}
        self.assertIn("overclaim_in_communication", codes)
        self.assertIn("synthetic_demand_claim", codes)

    def test_router_telemetry_and_dashboard_generation(self):
        self.cli("init", self.case, "Проверка маршрутизации")
        (self.case / "pilot.csv").write_text("arm,n,ok\nA,20,19\n", encoding="utf-8")
        (self.case / "screen.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        # Unregistered files + error recovery
        route = json.loads(
            self.cli("route", self.case, "--failed-tool", "web_fetch", "--error-reason", "HTTP 503")
        )
        self.assertEqual(route["recovery"]["failed_tool"], "web_fetch")
        self.assertEqual(route["primary_route"]["step"], "triage_new_inputs")
        self.assertEqual(route["workspace_scan"]["modalities"]["image_ux"], 1)

        # Telemetry recording
        tel = json.loads(
            self.cli(
                "telemetry", self.case, "--step", "intake", "--model", "fast-tier",
                "--seconds", "12.4", "--input-tokens", "1800", "--output-tokens", "220",
                "--human", "Уточнил порог маржи 45%",
            )
        )
        self.assertEqual(tel["events"], 1)
        self.assertEqual(tel["human_interventions"], ["Уточнил порог маржи 45%"])

        # Dashboard generation
        dash = json.loads(self.cli("dashboard", self.case))
        html_path = Path(dash["dashboard"])
        self.assertTrue(html_path.is_file())
        self.assertIn("Decision Ledger", html_path.read_text(encoding="utf-8"))

    def test_mcp_server_handles_ndjson_and_framed_requests(self):
        self.cli("init", self.case, "MCP тест")
        proc = subprocess.Popen(
            [sys.executable, "-m", "ledger", "mcp"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # 1. NDJSON initialize + tools/list + tools/call
            reqs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "ledger_status", "arguments": {"case": str(self.case)}
                }},
            ]
            for r in reqs:
                proc.stdin.write((json.dumps(r) + "\n").encode("utf-8"))
            proc.stdin.flush()

            init_resp = json.loads(proc.stdout.readline().decode("utf-8"))
            self.assertEqual(init_resp["result"]["serverInfo"]["name"], "decision-ledger")

            list_resp = json.loads(proc.stdout.readline().decode("utf-8"))
            tool_names = {t["name"] for t in list_resp["result"]["tools"]}
            self.assertIn("ledger_status", tool_names)
            self.assertIn("ledger_rates", tool_names)
            self.assertIn("ledger_kb_search", tool_names)

            call_resp = json.loads(proc.stdout.readline().decode("utf-8"))
            self.assertFalse(call_resp["result"]["isError"])
            status_payload = json.loads(call_resp["result"]["content"][0]["text"])
            self.assertEqual(status_payload["request"], "MCP тест")

            # 2. Content-Length framed ping
            ping_bytes = json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"}).encode("utf-8")
            proc.stdin.write(f"Content-Length: {len(ping_bytes)}\r\n\r\n".encode("ascii") + ping_bytes)
            proc.stdin.close()

            header = proc.stdout.readline()
            self.assertTrue(header.lower().startswith(b"content-length:"))
            length = int(header.split(b":", 1)[1].strip())
            proc.stdout.readline()  # blank line
            framed_body = json.loads(proc.stdout.read(length).decode("utf-8"))
            self.assertEqual(framed_body["id"], 4)
            self.assertEqual(proc.wait(timeout=5), 0)
        finally:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    unittest.main()
