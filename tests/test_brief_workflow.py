import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ledger.brief_calc import (
    evaluate_activation_14d,
    evaluate_brief_model,
    evaluate_gross_profit_bridge,
    render_brief_calc_markdown,
)
from ledger.pdf_text import extract_pdf_text
from ledger.strategies import _write_minimal_stdlib_pdf

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args, cwd=ROOT):
    return subprocess.run(
        [sys.executable, "-m", "ledger", *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


class BriefWorkflowTests(unittest.TestCase):
    def test_activation_14d_includes_unanswered_invites_in_denominator(self):
        # Exact instructional check from MegaFon sample brief:
        # 100 created, 60 accepted invite, 30 acted within 14d -> 30/100 = 30.0% (NOT 30/60 = 50%!)
        res = evaluate_activation_14d(
            {
                "created_groups": 100,
                "accepted_invite_groups": 60,
                "activated_within_14d_groups": 30,
                "target_rate": 0.60,
            }
        )
        self.assertEqual(res["created_groups_denominator"], 100)
        self.assertEqual(res["unanswered_or_declined_groups_in_denominator"], 40)
        self.assertAlmostEqual(res["activation_14d_pct"], 30.0)
        self.assertAlmostEqual(res["conditional_action_rate_given_accepted"], 0.5)
        self.assertFalse(res["meets_target"])
        self.assertEqual(res["additional_activated_needed_for_target"], 30)

    def test_gross_profit_bridge_enforces_calendar_cap_and_separate_capex(self):
        # Cohort starting in month 12 can give at most 1 extra month in the plan year
        with self.assertRaises(ValueError):
            evaluate_gross_profit_bridge(
                {
                    "retention_cohorts": [
                        {
                            "name": "december_cohort",
                            "launch_month": 12,
                            "retained_numbers": 500,
                            "avg_extra_months_in_plan_year": 2.0,
                        }
                    ]
                }
            )

        valid = evaluate_gross_profit_bridge(
            {
                "no_change_gross_profit_rub": 279_000_000,
                "target_gross_profit_rub": 285_000_000,
                "margin_per_extra_paid_month_rub": 500,
                "retention_cohorts": [
                    {
                        "name": "q3_rollout",
                        "launch_month": 7,
                        "retained_numbers": 4000,
                        "avg_extra_months_in_plan_year": 3.5,
                        "avg_extra_months_next_years": 4.0,
                    }
                ],
                "new_direct_opex_rub": 500_000,
                "capex_person_months": {"pm_design_analytics": 3.0, "dev_qa": 4.0},
            }
        )
        self.assertEqual(valid["breakeven_extra_paid_months_zero_opex"], 12000)
        self.assertEqual(valid["breakeven_extra_paid_months_with_opex"], 13000)
        self.assertEqual(valid["extra_paid_number_months_in_plan_year"], 14000.0)
        self.assertEqual(valid["projected_gross_profit_rub"], 285_500_000.0)
        self.assertTrue(valid["meets_target"])
        self.assertEqual(valid["capex_separate"]["total_person_months"], 7.0)
        self.assertIsNone(valid["capex_separate"]["capex_total_rub"])
        self.assertEqual(valid["capex_separate"]["max_capex_for_plan_year_payback_rub"], 6_500_000.0)

    def test_pdf_cli_and_unified_5part_brief_lifecycle(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            case = tmp / "brief-case"
            self.assertEqual(run_cli("init", case, "Разработать единый бриф из пяти частей по кварталам").returncode, 0)

            sample_pdf = case / "brief.pdf"
            _write_minimal_stdlib_pdf("Brief Sample\nTarget GP: 285 mln RUB\nAvailable households: 100000", sample_pdf)
            pdf_out = extract_pdf_text(sample_pdf)
            self.assertGreaterEqual(pdf_out["total_pages"], 1)
            self.assertIn("285", pdf_out["text"])

            self.assertEqual(run_cli("source", case, "brief_doc", "document", "brief.pdf").returncode, 0)

            # Route must detect PDF brief and recommend strategic-brief-5part
            route_res = json.loads(run_cli("route", case).stdout)
            steps = {s["step"]: s for s in route_res["pipeline"]}
            self.assertIn("brief_pdf_and_scorecard_audit", steps)
            self.assertEqual(steps["brief_pdf_and_scorecard_audit"]["skill"], "strategic-brief-5part")

            # Register brief_calc model JSON
            model_spec = {
                "pilot": {
                    "weekly_eligible_units": 120,
                    "recruitment_weeks": 6,
                    "observation_days": 14,
                    "control_event_rate": 0.12,
                    "target_test_event_rate": 0.08,
                },
                "scenarios": {
                    "main": {
                        "title": "Основной",
                        "activation_q4": {
                            "created_groups": 3000,
                            "accepted_invite_groups": 2250,
                            "activated_within_14d_groups": 1860,
                            "target_rate": 0.60,
                        },
                        "families": {
                            "market_total": 300000,
                            "available_base": 100000,
                            "start_connected": 6500,
                            "no_change_end_connected": 8000,
                            "target_end_connected": 12000,
                            "incremental_new_connected": 3100,
                            "incremental_churn_saved": 1100,
                            "no_change_active_last_month": 5000,
                            "target_active_last_month": 8000,
                            "projected_active_share": 0.68,
                        },
                        "multi_sim_owners": {
                            "market_total": 180000,
                            "available_base": 40000,
                            "start_connected": 800,
                            "no_change_end_connected": 1000,
                            "target_end_connected": 2000,
                            "incremental_new_connected": 900,
                            "incremental_churn_saved": 150,
                        },
                        "gross_profit": {
                            "no_change_gross_profit_rub": 279000000,
                            "target_gross_profit_rub": 285000000,
                            "margin_per_extra_paid_month_rub": 500,
                            "retention_cohorts": [
                                {
                                    "name": "family_h2",
                                    "launch_month": 7,
                                    "retained_numbers": 4200,
                                    "avg_extra_months_in_plan_year": 3.2,
                                }
                            ],
                            "new_direct_opex_rub": 400000,
                        },
                    },
                    "adverse": {
                        "title": "Неблагоприятный",
                        "activation_q4": {
                            "created_groups": 2600,
                            "accepted_invite_groups": 1750,
                            "activated_within_14d_groups": 1430,
                            "target_rate": 0.60,
                        },
                        "families": {
                            "market_total": 300000,
                            "available_base": 100000,
                            "start_connected": 6500,
                            "no_change_end_connected": 8000,
                            "target_end_connected": 12000,
                            "incremental_new_connected": 1800,
                            "incremental_churn_saved": 600,
                            "no_change_active_last_month": 5000,
                            "target_active_last_month": 8000,
                            "projected_active_share": 0.64,
                        },
                        "multi_sim_owners": {
                            "market_total": 180000,
                            "available_base": 40000,
                            "start_connected": 800,
                            "no_change_end_connected": 1000,
                            "target_end_connected": 2000,
                            "incremental_new_connected": 0,
                            "incremental_churn_saved": 0,
                        },
                        "gross_profit": {
                            "no_change_gross_profit_rub": 279000000,
                            "target_gross_profit_rub": 285000000,
                            "margin_per_extra_paid_month_rub": 500,
                            "retention_cohorts": [
                                {
                                    "name": "family_h2_slow",
                                    "launch_month": 8,
                                    "retained_numbers": 2200,
                                    "avg_extra_months_in_plan_year": 2.5,
                                }
                            ],
                            "new_direct_opex_rub": 400000,
                        },
                    },
                },
            }
            (case / "brief_model.json").write_text(json.dumps(model_spec, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(run_cli("source", case, "brief_model", "table", "brief_model.json").returncode, 0)

            md_table = render_brief_calc_markdown(evaluate_brief_model(model_spec))
            self.assertIn("285,32", md_table)
            self.assertIn("281,35", md_table)

            # Generate --template --brief, propose, and export --only-brief
            tpl_res = run_cli("draft", case, "--template", "--brief", "-o", "draft.yaml")
            self.assertEqual(tpl_res.returncode, 0, tpl_res.stderr)

            prop = run_cli("propose", case, "draft.yaml", "--strict-claims")
            self.assertEqual(prop.returncode, 0, prop.stderr)
            sha = json.loads(prop.stdout)["sha"]

            acc = run_cli("accept", case, "opt_change", "--expect", sha, "--by", "Директор направления", "--reason", "Утвердить пилот Q2")
            self.assertEqual(acc.returncode, 0, acc.stderr)

            exp_dir = tmp / "exported"
            exp = run_cli("export-strategies", case, "--only-brief", "-o", exp_dir)
            self.assertEqual(exp.returncode, 0, exp.stderr)
            exp_data = json.loads(exp.stdout)
            self.assertIsNone(exp_data["product_md"])
            self.assertTrue(Path(exp_data["brief_md"]).is_file())
            self.assertTrue(Path(exp_data["brief_pdf"]).is_file())
            brief_text = Path(exp_data["brief_md"]).read_text(encoding="utf-8")
            self.assertIn("## 1. Клиент и его проблема", brief_text)
            self.assertIn("## 5. Ресурсы, риски и решение руководителя", brief_text)
            self.assertIn("285,32", brief_text)

    def test_mcp_exposes_pdf_and_brief_calc_tools(self):
        reqs = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "ledger_brief_calc",
                            "arguments": {
                                "format": "json",
                                "spec": {
                                    "scenarios": {
                                        "main": {
                                            "activation_q4": {
                                                "created_groups": 100,
                                                "accepted_invite_groups": 60,
                                                "activated_within_14d_groups": 30,
                                            },
                                            "families": {
                                                "market_total": 300000,
                                                "available_base": 100000,
                                                "start_connected": 6500,
                                                "no_change_end_connected": 8000,
                                                "target_end_connected": 12000,
                                            },
                                            "multi_sim_owners": {
                                                "market_total": 180000,
                                                "available_base": 40000,
                                                "start_connected": 800,
                                                "no_change_end_connected": 1000,
                                                "target_end_connected": 2000,
                                            },
                                            "gross_profit": {},
                                        }
                                    }
                                },
                            },
                        },
                    }
                ),
            ]
        ) + "\n"
        proc = subprocess.run(
            [sys.executable, "-m", "ledger", "mcp"],
            cwd=ROOT,
            input=reqs,
            capture_output=True,
            text=True,
            check=True,
        )
        lines = [json.loads(ln) for ln in proc.stdout.strip().splitlines()]
        tool_names = {t["name"] for t in lines[0]["result"]["tools"]}
        self.assertEqual(len(tool_names), 14)
        self.assertIn("ledger_pdf", tool_names)
        self.assertIn("ledger_brief_calc", tool_names)
        payload = json.loads(lines[1]["result"]["content"][0]["text"])
        self.assertEqual(payload["scenarios"]["main"]["activation_q4"]["activation_14d_pct"], 30.0)


if __name__ == "__main__":
    unittest.main()
