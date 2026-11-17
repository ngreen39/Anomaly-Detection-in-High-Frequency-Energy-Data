import contextlib
import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.audit_data_quality import HEADER, audit, file_hash


class AuditTests(unittest.TestCase):
    def run_fixture(self, rows):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "fixture.txt"
        with source.open("w", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(HEADER)
            writer.writerows(rows)
        original = file_hash(source)
        with contextlib.redirect_stdout(io.StringIO()):
            summary = audit(source, root / "reports")
        self.assertEqual(file_hash(source), original)
        return root / "reports", summary

    def read_csv(self, output, name):
        with (output / name).open(newline="") as handle:
            return list(csv.DictReader(handle))

    def test_known_duplicates_gaps_invalid_values_and_runs(self):
        normal = ["1"] * 7
        missing = ["?"] * 7
        rows = [
            ["01/01/2020", "00:00:00", *normal],
            ["01/01/2020", "00:01:00", *missing],
            ["01/01/2020", "00:02:00", *([""] * 7)],
            ["01/01/2020", "00:02:00", *([""] * 7)],
            ["01/01/2020", "00:04:00", " ? ", *normal[1:]],
            ["31/02/2020", "00:00:00", "junk", "-2", "1e999", "-inf", "NaN", "1", "2"],
            ["01/01/2020", "00:05:00", *normal],
            ["01/01/2020", "00:05:00", "2", *normal[1:]],
            ["01/01/2020", "00:06:30", *normal],
            ["01/01/2020", "00:07:00", *missing],
        ]
        output, summary = self.run_fixture(rows)
        self.assertEqual(summary["counts"], {
            "source_rows": 10, "duplicate_rows": 1, "duplicate_timestamps": 2,
            "invalid_timestamps": 1, "off_minute_timestamps": 1,
            "missing_measurement_rows": 5, "all_measurements_missing_rows": 4,
            "missing_measurement_cells": 29, "invalid_numeric_cells": 2,
            "negative_cells": 1, "infinite_cells": 2, "absent_timestamp_rows": 2,
            "missing_run_count": 3, "missing_minutes_in_runs": 4,
            "longest_missing_run_minutes": 2,
        })
        self.assertEqual(summary["exception_records"], 41)
        runs = self.read_csv(output, "missing_runs.csv")
        self.assertEqual([(r["start"], r["end"], r["number_of_missing_readings"], r["duration_minutes"])
                          for r in runs], [
            ("2020-01-01 00:01:00", "2020-01-01 00:02:00", "2", "2"),
            ("2020-01-01 00:04:00", "2020-01-01 00:04:00", "1", "1"),
            ("2020-01-01 00:07:00", "2020-01-01 00:07:00", "1", "1"),
        ])
        exceptions = self.read_csv(output, "exceptions.csv")
        self.assertEqual({e["timestamp"] for e in exceptions if e["rule"] == "absent_timestamp"},
                         {"2020-01-01 00:03:00", "2020-01-01 00:06:00"})
        self.assertTrue(all(not e["source_row_id"] for e in exceptions if e["rule"] == "absent_timestamp"))
        partial = next(e for e in exceptions if e["source_row_id"] == "5")
        self.assertEqual((partial["observed_value"], partial["missing_run_id"]), (" ? ", "2"))
        self.assertEqual({e["missing_run_id"] for e in exceptions
                          if e["source_row_id"] == "4" and e["rule"] == "missing_measurement"}, {"1"})
        self.assertEqual(summary["sql_monthly_totals"]["source_rows"], 10)
        self.assertTrue(all(summary["reconciliation"].values()))
        # A second run replaces reports deterministically rather than appending.
        before = {p.name: p.read_bytes() for p in output.glob("*.csv")}
        with contextlib.redirect_stdout(io.StringIO()):
            audit(output.parent / "fixture.txt", output)
        self.assertEqual(before, {p.name: p.read_bytes() for p in output.glob("*.csv")})

    def test_month_boundary_separates_absent_from_missing_measurements(self):
        output, summary = self.run_fixture([
            ["31/01/2020", "23:59:00", *(["?"] * 7)],
            ["01/02/2020", "00:01:00", *(["1"] * 7)],
        ])
        months = self.read_csv(output, "missingness_by_month.csv")
        self.assertEqual([(m["month"], m["missing_measurement_rows"], m["absent_timestamp_rows"])
                          for m in months], [("2020-01", "1", "0"), ("2020-02", "0", "1")])
        self.assertEqual(summary["counts"]["missing_run_count"], 1)

    def test_unplaceable_missing_rows_are_retained(self):
        output, summary = self.run_fixture([
            ["bad-date", "bad-time", *(["?"] * 7)],
            ["01/01/2020", "00:00:30", *(["?"] * 7)],
        ])
        self.assertEqual(summary["missing_rows_without_grid_timestamp"], 2)
        self.assertEqual(summary["counts"]["missing_measurement_rows"], 2)
        self.assertEqual(summary["counts"]["missing_run_count"], 0)
        self.assertEqual(summary["timestamp_range"], {"start": None, "end": None})
        self.assertTrue(all(not e["missing_run_id"] for e in self.read_csv(output, "exceptions.csv")))

    def test_empty_source_has_zero_counts_and_header_only_reports(self):
        output, summary = self.run_fixture([])
        self.assertTrue(all(v == 0 for v in summary["counts"].values()))
        self.assertEqual(self.read_csv(output, "exceptions.csv"), [])
        self.assertEqual(self.read_csv(output, "missing_runs.csv"), [])

    def test_saved_queries_can_be_rerun_on_database(self):
        from scripts.audit_data_quality import SQL_DIR
        output, summary = self.run_fixture([["01/01/2020", "00:00:00", *(["?"] * 7)]])
        with contextlib.closing(sqlite3.connect(output / "audit.sqlite3")) as connection:
            cursor = connection.execute((SQL_DIR / "reconcile_totals.sql").read_text())
            actual = dict(zip([col[0] for col in cursor.description], cursor.fetchone()))
        self.assertEqual(actual, summary["counts"])


if __name__ == "__main__":
    unittest.main()
