import tempfile
import unittest
from pathlib import Path

from ledger.economics import evaluate_economics
from ledger.stats import wilson_interval
from ledger.table import summarize_rates

ROOT = Path(__file__).resolve().parents[1]


class AnalyticsTests(unittest.TestCase):
    def test_wilson_interval_exposes_small_sample_risk_at_zero_events(self):
        # 0/20 looks like 0% point estimate, but 95% Wilson upper bound exceeds 16% (> 3% guardrail)
        low, high = wilson_interval(0, 20)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.16)
        self.assertLess(high, 0.17)

    def test_retail_initial_and_recount_guardrails_detect_masked_failures(self):
        initial_csv = ROOT / "examples/retail-backup/initial/pilot.csv"
        recount_csv = ROOT / "examples/retail-backup/later/recount.csv"
        guardrails = ["checkout_completed>=0.90", "false_success<=0.03"]

        before = summarize_rates(
            initial_csv,
            group="group",
            segment="cohort",
            denominator="drills",
            numerators=["network_recovered", "checkout_completed", "false_success"],
            ci=True,
            guardrails=guardrails,
        )
        # Before recount: legacy_gateway has 0/20 false_success -> point passes, CI flags uncertainty!
        gw_before = before["segments"]["legacy_gateway"]["managed_failover"]["numerators"]["false_success"]
        self.assertEqual(gw_before["guardrails"]["<=0.03"], "uncertain_small_sample")
        kinds_before = {a["kind"] for a in before["anomalies"] if a["group"] == "managed_failover"}
        self.assertIn("masked_segment_uncertainty", kinds_before)

        after = summarize_rates(
            recount_csv,
            group="group",
            segment="cohort",
            denominator="drills",
            numerators=["network_recovered", "checkout_completed", "false_success"],
            ci=True,
            guardrails=guardrails,
        )
        # Overall managed_failover still passes both thresholds on point estimate (94.23%, 2.69%),
        # while legacy_gateway violates both (70%, 30%) -> masked_segment_failure!
        masked = [
            a for a in after["anomalies"]
            if a["kind"] == "masked_segment_failure" and a["group"] == "managed_failover"
        ]
        self.assertEqual({a["metric"] for a in masked}, {"checkout_completed", "false_success"})

    def test_simpson_paradox_reversal_is_automatically_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "simpson.csv"
            p.write_text(
                "arm,segment,users,success\n"
                "A,mobile,100,90\n"
                "B,mobile,10,10\n"
                "A,desktop,10,1\n"
                "B,desktop,100,20\n",
                encoding="utf-8",
            )
            res = summarize_rates(
                p, group="arm", segment="segment", denominator="users", numerators=["success"], ci=True
            )
            reversals = [a for a in res["anomalies"] if a["kind"] == "simpson_reversal"]
            self.assertEqual(len(reversals), 1)
            self.assertEqual(reversals[0]["overall_leader"], "A")
            self.assertEqual(reversals[0]["segment_leader"], "B")

    def test_unit_economics_computes_headroom_payback_and_stress_bounds(self):
        spec = {
            "currency": "RUB",
            "unit": "store_month",
            "min_margin_rate": 0.45,
            "plans": {
                "self_install": {
                    "title": "Самостоятельный комплект",
                    "monthly_revenue": 1290,
                    "recurring_costs": {"sim": 250, "support": 100},
                    "upfront_costs": {"router": 2400},
                    "amortize_months": 12,
                },
                "managed_failover": {
                    "title": "Настроенный резерв",
                    "monthly_revenue": 2490,
                    "recurring_costs": {"sim": 300, "support_recheck": 400},
                    "upfront_costs": {"router": 3600, "install": 2400},
                    "amortize_months": 12,
                    "unknowns": ["Стоимость доработки старых шлюзов"],
                },
            },
        }
        report = evaluate_economics(spec)
        self.assertEqual(report["viable_plans"], ["managed_failover", "self_install"])
        mf = report["plans"]["managed_failover"]
        self.assertEqual(mf["direct_cost"], 1200.0)
        self.assertEqual(mf["contribution"], 1290.0)
        self.assertAlmostEqual(mf["contribution_rate"], 1290 / 2490, places=5)
        self.assertEqual(mf["max_allowable_cost"], 1369.5)
        self.assertEqual(mf["cost_headroom"], 169.5)
        # Cost +15% => 1200 * 1.15 = 1380 > 1369.5 => breaches 45% target!
        self.assertFalse(mf["sensitivity"]["cost_plus_15pct"]["meets_target"])


if __name__ == "__main__":
    unittest.main()
