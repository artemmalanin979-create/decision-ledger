import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.case = Path(self.workspace.name) / "case"
        self.call("init", "Как поступить?")
        self.raw = self.case / "sample.csv"
        self.raw.write_text("eligible,events\n100,4\n")
        self.digest = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        self.call("source", "pilot", "synthetic", "sample.csv")

    def call(self, command, *arguments, ok=True):
        result = subprocess.run(
            [sys.executable, "-m", "ledger", command, str(self.case), *arguments],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0 if ok else 2, result.stderr)
        return result.stdout if ok else result.stderr

    def proposal(self):
        return {
            "title": "Пилот",
            "summary": "Проверим предупреждение",
            "reviewed": {"pilot": {"sha": self.digest, "note": "Контроль и пилот"}},
            "findings": {
                "f1": {"kind": "observation", "text": "4 случая в модели",
                       "citations": [{"source": "pilot", "locator": "строка 2"}]},
                "f2": {"kind": "inference", "text": "Есть риск", "depends_on": ["f1"]},
                "f3": {"kind": "unknown", "text": "Спрос не измерен"},
            },
            "options": {
                "keep": {"title": "Без изменений", "type": "keep", "case": "База",
                         "objection": "Без эффекта", "depends_on": ["f3"]},
                "warn": {"title": "Предупреждение", "type": "change", "case": "Пилот",
                         "objection": "Усталость", "depends_on": ["f2"]},
                "close": {"title": "Закрыть", "type": "close", "case": "Меньше затрат",
                          "objection": "Теряем канал", "depends_on": ["f3"]},
            },
            "recommended": "warn",
            "strategy": {
                name: {"text": f"Рабочая стратегия {name}", "depends_on": ["f2"]}
                for name in ("product", "communication", "experiment")
            },
            "actions": [{"id": "check", "owner": "PM", "due": "2026-11-30",
                         "task": "Измерить вред", "trigger": "Рост пропусков"}],
        }

    def propose(self, data, *, ok=True):
        draft = self.case / "proposal.json"
        draft.write_text(json.dumps(data))
        return self.call("propose", "proposal.json", ok=ok)

    def approve(self):
        self.propose(self.proposal())
        fingerprint = self.status()["draft"]["sha"]
        self.accept(fingerprint)
        return fingerprint

    def accept(self, fingerprint, *, ok=True):
        return self.call("accept", "warn", "--by", "PM", "--reason", "Пилот", "--expect", fingerprint, ok=ok)

    def test_replacing_draft_with_same_option_invalidates_confirmation(self):
        original = self.proposal()
        self.propose(original)
        fingerprint = self.status()["draft"]["sha"]
        original["summary"] = "Другие условия"
        self.propose(original)
        self.accept(fingerprint, ok=False)
        self.assertIsNone(self.status()["approved"])
        self.accept(self.status()["draft"]["sha"])

    def status(self):
        return json.loads(self.call("status"))

    def test_changed_source_marks_transitive_findings_and_only_dependent_option(self):
        self.approve()
        self.raw.write_text("eligible,events\n100,9\n")
        impact = self.status()["approved_impact"]
        self.assertEqual(impact["sources"], ["pilot"])
        self.assertEqual(impact["findings"], ["f1", "f2"])
        self.assertEqual(impact["options"], ["warn"])
        self.assertEqual(impact["strategy"], ["communication", "experiment", "product"])

    def test_new_source_and_stale_draft_block_acceptance(self):
        self.propose(self.proposal())
        fingerprint = self.status()["draft"]["sha"]
        (self.case / "new.md").write_text("Новые сведения")
        self.call("source", "new", "document", "new.md")
        self.assertEqual(self.status()["draft_impact"]["unreviewed"], ["new"])
        self.accept(fingerprint, ok=False)
        self.assertIsNone(self.status()["approved"])

    def test_history_retains_old_snapshot_and_duplicate_accept_is_rejected(self):
        fingerprint = self.approve()
        self.accept(fingerprint, ok=False)
        self.raw.write_text("eligible,events\n100,9\n")
        self.call("source", "pilot", "synthetic", "sample.csv")
        self.assertIn("100,4", self.call("evidence", self.digest))
        self.assertIn("4 случая в модели", self.call("view", "--decision", "1"))
        self.assertEqual(self.status()["approved"]["revision"], 1)

    def test_unsupported_fact_and_cycle_rejected(self):
        data = self.proposal()
        data["findings"]["f1"].pop("citations")
        self.propose(data, ok=False)
        data = self.proposal()
        data["findings"]["f1"]["depends_on"] = ["f2"]
        self.propose(data, ok=False)
        self.assertIsNone(self.status()["draft"])

    def test_checkpoint_and_actions_survive_new_processes(self):
        self.call("checkpoint", "research", "Проверить рыночные источники")
        self.approve()
        self.call("done", "check", "--decision", "1", "--note", "Протокол подготовлен")
        status = self.status()
        self.assertEqual(status["checkpoint"]["stage"], "research")
        self.assertEqual(status["approved"]["actions"][0]["completion"]["note"], "Протокол подготовлен")
        self.call("done", "check", "--decision", "1", "--note", "Повтор", ok=False)

    def test_database_symlink_is_rejected(self):
        alias = self.case.parent / "alias"
        alias.mkdir()
        (alias / "case.sqlite3").symlink_to(self.case / "case.sqlite3")
        result = subprocess.run(
            [sys.executable, "-m", "ledger", "checkpoint", str(alias), "review", "unexpected"],
            cwd=ROOT, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.status()["checkpoint"]["stage"], "intake")

    def test_completion_stays_on_the_approved_revision(self):
        self.approve()
        proposal = self.proposal()
        proposal["actions"][0]["owner"] = "QA"
        self.propose(proposal)
        self.accept(self.status()["draft"]["sha"])
        self.call("done", "check", "--decision", "1", "--note", "PM finished", ok=False)
        self.assertEqual(self.status()["approved"]["actions"][0]["status"], "open")
        self.call("done", "check", "--decision", "2", "--note", "QA finished")
        self.assertNotIn("QA finished", self.call("view", "--decision", "1"))
        self.assertIn("QA finished", self.call("view", "--decision", "2"))

    def test_evidence_replays_exact_bytes(self):
        content = b"sample  \r\n\r\n"
        self.raw.write_bytes(content)
        digest = json.loads(self.call("source", "pilot", "synthetic", "sample.csv"))["sha"]
        result = subprocess.run(
            [sys.executable, "-m", "ledger", "evidence", str(self.case), digest],
            cwd=ROOT, capture_output=True, check=True,
        )
        self.assertEqual(result.stdout, content)

    def test_retired_source_can_be_replaced_without_losing_history(self):
        self.approve()
        self.raw.unlink()
        self.call("retire", "pilot", "--reason", "Отозвано владельцем")
        self.assertEqual(self.status()["approved_impact"]["findings"], ["f1", "f2"])
        alternate = self.case / "replacement.csv"
        alternate.write_text("eligible,events\n100,5\n")
        replacement = json.loads(self.call("source", "replacement", "synthetic", "replacement.csv"))
        data = self.proposal()
        data["reviewed"] = {"replacement": {"sha": replacement["sha"], "note": "Сверенные данные"}}
        data["findings"]["f1"]["citations"][0]["source"] = "replacement"
        self.propose(data)
        self.accept(self.status()["draft"]["sha"])
        self.assertEqual(self.status()["approved_impact"]["sources"], [])
        self.assertIn("100,4", self.call("evidence", self.digest))

    def test_new_staging_cannot_reuse_previous_approval(self):
        previous = self.approve()
        self.propose(self.proposal())
        self.accept(previous, ok=False)
        self.assertEqual(len(self.status()["history"]), 1)
        self.accept(self.status()["draft"]["sha"])

    def test_retargeted_source_is_detected(self):
        alias = self.case / "current.csv"
        alias.symlink_to(self.raw)
        self.call("source", "pilot", "synthetic", "current.csv")
        self.approve()
        alternate = self.case / "next.csv"
        alternate.write_text("eligible,events\n100,8\n")
        alias.unlink()
        alias.symlink_to(alternate)
        self.assertEqual(self.status()["approved_impact"]["sources"], ["pilot"])

    def test_source_path_cannot_escape_case(self):
        (self.case.parent / "private.md").write_text("PRIVATE")
        self.call("source", "external", "document", "../private.md", ok=False)
        self.assertNotIn("external", self.status()["sources"])


if __name__ == "__main__":
    unittest.main()
