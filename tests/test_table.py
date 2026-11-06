import tempfile
import unittest
from pathlib import Path

from ledger.table import summarize_rates


class TableTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "experiment.csv"

    def summarize(self, contents, **columns):
        self.path.write_text(contents, encoding="utf-8")
        options = {
            "group": "arm",
            "segment": None,
            "denominator": "users",
            "numerators": ["success"],
        }
        options.update(columns)
        return summarize_rates(self.path, **options)

    def test_unequal_segment_sizes_can_reverse_the_overall_comparison(self):
        report = self.summarize(
            "\ufeff arm , segment , users , success , paid \n"
            " A , mobile ,100,90,12\n"
            " B , mobile ,10,10,2\n"
            " A , desktop ,10,1,0\n"
            " B , desktop ,100,20,1\n",
            segment="segment",
            numerators=["success", "paid"],
        )

        self.assertEqual(report["rows"], 4)
        self.assertEqual(
            report["overall"],
            {
                "A": {
                    "denominator": 110,
                    "numerators": {
                        "success": {"count": 91, "rate": 91 / 110},
                        "paid": {"count": 12, "rate": 12 / 110},
                    },
                },
                "B": {
                    "denominator": 110,
                    "numerators": {
                        "success": {"count": 30, "rate": 30 / 110},
                        "paid": {"count": 3, "rate": 3 / 110},
                    },
                },
            },
        )
        mobile = report["segments"]["mobile"]
        desktop = report["segments"]["desktop"]
        self.assertEqual(mobile["A"]["denominator"], 100)
        self.assertEqual(mobile["B"]["denominator"], 10)
        self.assertEqual(desktop["A"]["denominator"], 10)
        self.assertEqual(desktop["B"]["denominator"], 100)
        self.assertEqual(mobile["A"]["numerators"]["success"], {"count": 90, "rate": 0.9})
        self.assertEqual(mobile["B"]["numerators"]["success"], {"count": 10, "rate": 1.0})
        self.assertEqual(desktop["A"]["numerators"]["success"], {"count": 1, "rate": 0.1})
        self.assertEqual(desktop["B"]["numerators"]["success"], {"count": 20, "rate": 0.2})

    def test_zero_denominators_produce_null_rates(self):
        report = self.summarize(
            "arm,segment,users,success\nA,empty,0,0\nA,active,2,1\nB,empty,0,0\n",
            segment="segment",
        )

        empty = {"denominator": 0, "numerators": {"success": {"count": 0, "rate": None}}}
        self.assertEqual(report["segments"]["empty"], {"A": empty, "B": empty})
        self.assertEqual(report["overall"]["B"], empty)
        self.assertEqual(
            report["overall"]["A"],
            {"denominator": 2, "numerators": {"success": {"count": 1, "rate": 0.5}}},
        )

    def test_header_only_returns_empty_summary(self):
        report = self.summarize("arm,users,success\n")

        self.assertEqual(report, {"rows": 0, "overall": {}, "segments": {}})

    def test_missing_or_ambiguous_columns_are_rejected(self):
        for csv, options, error in [
            ("", {}, "header"),
            ("arm,users\nA,10\n", {}, "success"),
            ("arm,users,success, users \nA,10,1,20\n", {}, "Duplicate"),
            ("arm,users,success\nA,10,1\n", {"segment": "arm"}, "distinct"),
            ("arm,users,success\nA,10,1\n", {"numerators": ["users"]}, "distinct"),
            ("arm,users,success\nA,10,1\n", {"numerators": ["success", "success"]}, "distinct"),
        ]:
            with self.subTest(csv=csv, options=options):
                with self.assertRaisesRegex(ValueError, error):
                    self.summarize(csv, **options)

    def test_bad_rows_are_rejected_before_aggregation(self):
        for row, error in [
            ("A,10,", "success"),
            ("A,10,1.5", "success"),
            ("A,10,1_0", "success"),
            ("A,10,-1", "success"),
            ("A,-10,0", "users"),
            ("A,0,1", "exceeds"),
            ("A,1,2", "exceeds"),
            ("A,10", "fields"),
            ("A,10,1,extra", "fields"),
            (" ,10,1", "arm"),
            ('"A,10,1', "CSV"),
        ]:
            with self.subTest(row=row):
                with self.assertRaisesRegex(ValueError, error):
                    self.summarize("arm,users,success\nA,100,0\n" + row + "\n")


if __name__ == "__main__":
    unittest.main()
