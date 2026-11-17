"""Read-only source audit; all derived outputs go to --output-dir.

Only Python's standard library is used. No imputation or model alerts are made.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

FIELDS = (
    "Global_active_power", "Global_reactive_power", "Voltage",
    "Global_intensity", "Sub_metering_1", "Sub_metering_2", "Sub_metering_3",
)
HEADER = ["Date", "Time", *FIELDS]
EPOCH = datetime(1970, 1, 1)
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
EXCEPTION_HEADER = [
    "source_row_id", "timestamp", "field", "rule", "observed_value",
    "missing_run_id",
]
RUN_HEADER = [
    "missing_run_id", "start", "end", "number_of_missing_readings",
    "duration_minutes",
]


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stamp(seconds: int) -> str:
    return (EPOCH + timedelta(seconds=seconds)).isoformat(sep=" ")


def write_csv(path: Path, header, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def python_runs(minutes: set[int]) -> list[tuple]:
    runs = []
    start = end = None
    for minute in sorted(minutes):
        if start is not None and minute != end + 1:
            runs.append((len(runs) + 1, stamp(start * 60), stamp(end * 60),
                         end - start + 1, end - start + 1))
            start = None
        if start is None:
            start = minute
        end = minute
    if start is not None:
        runs.append((len(runs) + 1, stamp(start * 60), stamp(end * 60),
                     end - start + 1, end - start + 1))
    return runs


def audit(source: Path, output: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    if sqlite3.sqlite_version_info < (3, 25, 0):
        raise RuntimeError("SQLite 3.25+ is required for the saved SQL queries")
    original_hash = file_hash(source)
    # Validate the schema before creating or replacing any report files.
    with source.open(newline="", encoding="utf-8-sig") as handle:
        if next(csv.reader(handle, delimiter=";"), None) != HEADER:
            raise ValueError(f"Expected semicolon-delimited columns: {HEADER}")
    output.mkdir(parents=True, exist_ok=True)
    db_path = output / "audit.sqlite3"
    if source.parent == output and source.name in {
        "audit.sqlite3", "audit_summary.json", "audit_summary.md", "missing_runs.csv",
        "exceptions.csv", "missingness_by_month.csv", "longest_missing_runs.csv",
    }:
        raise ValueError("Source path overlaps an output artifact")
    # Replace only the explicitly named derived database, never the source.
    db_path.unlink(missing_ok=True)
    counts = dict.fromkeys([
        "source_rows", "duplicate_rows", "duplicate_timestamps", "invalid_timestamps",
        "off_minute_timestamps", "missing_measurement_rows", "all_measurements_missing_rows",
        "missing_measurement_cells", "invalid_numeric_cells", "negative_cells",
        "infinite_cells", "absent_timestamp_rows",
    ], 0)
    per_field = {field: dict.fromkeys(["missing", "invalid_numeric", "negative", "infinite"], 0)
                 for field in FIELDS}
    seen_rows, seen_seconds, missing_minutes = set(), set(), set()
    missing_unplaced_rows = 0
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript("""
            CREATE TABLE readings (
                source_row_id INTEGER PRIMARY KEY, timestamp TEXT, minute_epoch INTEGER,
                missing_count INTEGER, invalid_count INTEGER, negative_count INTEGER,
                infinity_count INTEGER, duplicate_row INTEGER, duplicate_timestamp INTEGER
            );
            CREATE TABLE exceptions (
                source_row_id INTEGER, timestamp TEXT, field TEXT, rule TEXT,
                observed_value TEXT
            );
            CREATE TABLE absent_timestamps (timestamp TEXT PRIMARY KEY);
        """)
        reading_batch, exception_batch = [], []

        def flush():
            connection.executemany("INSERT INTO readings VALUES (?,?,?,?,?,?,?,?,?)", reading_batch)
            connection.executemany("INSERT INTO exceptions VALUES (?,?,?,?,?)", exception_batch)
            reading_batch.clear()
            exception_batch.clear()

        with source.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle, delimiter=";")
            next(reader)
            for row_id, row in enumerate(reader, start=1):
                if len(row) != len(HEADER):
                    raise ValueError(f"Source record {row_id}: expected 9 fields, got {len(row)}")
                counts["source_rows"] += 1
                raw_key = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
                duplicate_row = int(raw_key in seen_rows)
                seen_rows.add(raw_key)
                counts["duplicate_rows"] += duplicate_row
                raw_timestamp = row[0] + " " + row[1]
                timestamp = None
                seconds = minute = None
                duplicate_timestamp = 0
                try:
                    parsed = datetime.strptime(raw_timestamp, "%d/%m/%Y %H:%M:%S")
                    timestamp = parsed.isoformat(sep=" ")
                    seconds = int((parsed - EPOCH).total_seconds())
                    duplicate_timestamp = int(seconds in seen_seconds)
                    seen_seconds.add(seconds)
                    counts["duplicate_timestamps"] += duplicate_timestamp
                    if seconds % 60 == 0:
                        minute = seconds // 60
                    else:
                        counts["off_minute_timestamps"] += 1
                        exception_batch.append((row_id, timestamp, "Datetime", "off_minute_timestamp", raw_timestamp))
                except ValueError:
                    counts["invalid_timestamps"] += 1
                    exception_batch.append((row_id, None, "Datetime", "invalid_timestamp", raw_timestamp))
                if duplicate_row:
                    exception_batch.append((row_id, timestamp, "__row__", "duplicate_row", raw_key))
                if duplicate_timestamp:
                    exception_batch.append((row_id, timestamp, "Datetime", "duplicate_timestamp", raw_timestamp))
                missing = invalid = negative = infinite = 0
                for field, raw_value in zip(FIELDS, row[2:]):
                    token = raw_value.strip()
                    rule = None
                    if token in {"", "?"}:
                        missing += 1
                        per_field[field]["missing"] += 1
                        rule = "missing_measurement"
                    else:
                        try:
                            value = float(token)
                        except (ValueError, OverflowError):
                            value = math.nan
                        if math.isnan(value):
                            invalid += 1
                            per_field[field]["invalid_numeric"] += 1
                            rule = "invalid_numeric"
                        elif math.isinf(value):
                            infinite += 1
                            per_field[field]["infinite"] += 1
                            rule = "infinite_value"
                        elif value < 0:
                            negative += 1
                            per_field[field]["negative"] += 1
                            rule = "negative_value"
                    if rule:
                        exception_batch.append((row_id, timestamp, field, rule, raw_value))
                counts["missing_measurement_rows"] += int(missing > 0)
                counts["all_measurements_missing_rows"] += int(missing == len(FIELDS))
                counts["missing_measurement_cells"] += missing
                counts["invalid_numeric_cells"] += invalid
                counts["negative_cells"] += negative
                counts["infinite_cells"] += infinite
                if missing:
                    if minute is not None:
                        missing_minutes.add(minute)
                    else:
                        missing_unplaced_rows += 1
                reading_batch.append((row_id, timestamp, minute, missing, invalid,
                                      negative, infinite, duplicate_row, duplicate_timestamp))
                if row_id % 10000 == 0:
                    flush()
                if row_id % 500000 == 0:
                    print(f"Audited {row_id:,} source rows", flush=True)
        flush()
        # Only aligned timestamps define the expected one-minute grid.
        # Compare sorted unique points; never treat duplicate rows as new minutes.
        previous = first = last = None
        for seconds in sorted(seen_seconds):
            if seconds % 60:
                continue
            if first is None:
                first = seconds
            if previous is not None:
                for absent in range(previous + 60, seconds, 60):
                    absent_stamp = stamp(absent)
                    counts["absent_timestamp_rows"] += 1
                    connection.execute("INSERT INTO absent_timestamps VALUES (?)", (absent_stamp,))
                    connection.execute("INSERT INTO exceptions VALUES (?,?,?,?,?)",
                                       (None, absent_stamp, "Datetime", "absent_timestamp", ""))
            previous = last = seconds
        runs = python_runs(missing_minutes)
        counts.update(missing_run_count=len(runs), missing_minutes_in_runs=len(missing_minutes),
                      longest_missing_run_minutes=max((r[4] for r in runs), default=0))
        connection.execute("CREATE INDEX readings_missing_minutes ON readings(minute_epoch) WHERE missing_count > 0")
        queries = {name: (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")
                   for name in ["missingness_by_month", "missing_runs", "longest_missing_runs", "reconcile_totals"]}
        sql_runs = list(connection.execute(queries["missing_runs"]))
        if sql_runs != runs:
            raise AssertionError("SQL and Python missing runs disagree")
        connection.execute("CREATE TABLE missing_runs (missing_run_id INTEGER PRIMARY KEY, start TEXT, end TEXT, number_of_missing_readings INTEGER, duration_minutes INTEGER)")
        connection.executemany("INSERT INTO missing_runs VALUES (?,?,?,?,?)", sql_runs)
        cursor = connection.execute(queries["reconcile_totals"])
        sql_totals = {col[0]: (value or 0) for col, value in zip(cursor.description, cursor.fetchone())}
        if counts != sql_totals:
            raise AssertionError(f"SQL/Python totals disagree: Python={counts}, SQL={sql_totals}")
        cursor = connection.execute(queries["missingness_by_month"])
        month_header = [col[0] for col in cursor.description]
        months = cursor.fetchall()
        month_totals = dict(zip(month_header[1:], [sum(r[i] for r in months) for i in range(1, 5)]))
        if any(month_totals[k] != counts[k] for k in month_totals):
            raise AssertionError("Monthly SQL totals disagree with Python")
        write_csv(output / "missingness_by_month.csv", month_header, months)
        write_csv(output / "missing_runs.csv", RUN_HEADER, runs)
        write_csv(output / "longest_missing_runs.csv", RUN_HEADER,
                  connection.execute(queries["longest_missing_runs"]))
        run_by_stamp = {}
        for run_id, start, end, _, _ in runs:
            point = datetime.fromisoformat(start)
            while point <= datetime.fromisoformat(end):
                run_by_stamp[point.isoformat(sep=" ")] = run_id
                point += timedelta(minutes=1)
        exception_count = connection.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0]

        def exception_rows():
            for row in connection.execute("SELECT * FROM exceptions ORDER BY source_row_id IS NULL, source_row_id, field, rule, timestamp"):
                run_id = run_by_stamp.get(row[1], "") if row[3] == "missing_measurement" else ""
                yield (*row, run_id)

        write_csv(output / "exceptions.csv", EXCEPTION_HEADER, exception_rows())
        connection.commit()
        if file_hash(source) != original_hash:
            raise RuntimeError("Source changed during audit; discard these outputs and rerun")
        summary = {
            "source_file": source.name, "source_sha256": original_hash,
            "sql_sha256": {name + ".sql": file_hash(SQL_DIR / (name + ".sql")) for name in queries},
            "counts": counts, "per_field": per_field,
            "timestamp_range": {"start": stamp(first) if first is not None else None,
                                "end": stamp(last) if last is not None else None},
            "missing_rows_without_grid_timestamp": missing_unplaced_rows,
            "runs_longer_than_60_minutes": sum(r[4] > 60 for r in runs),
            "runs_longer_than_1440_minutes": sum(r[4] > 1440 for r in runs),
            "exception_records": exception_count,
            "sql_totals": sql_totals, "sql_monthly_totals": month_totals,
            "reconciliation": {"totals_match": True, "monthly_totals_match": True, "all_runs_match": True},
        }
        (output / "audit_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        pct = 100 * counts["missing_measurement_rows"] / counts["source_rows"] if counts["source_rows"] else 0
        lines = ["# Data-quality audit", "", f"Source: `{source.name}`",
                 f"SHA-256: `{original_hash}`", "",
                 "| Check | Count |", "| --- | ---: |",
                 *[f"| {key.replace('_', ' ')} | {value:,} |" for key, value in counts.items()], "",
                 f"Missing-measurement source rows: {pct:.4f}%.",
                 f"Runs longer than one day: {summary['runs_longer_than_1440_minutes']}.",
                 f"Exception records: {exception_count:,} (multiple exceptions may describe one row).", "",
                 "SQL/Python totals, monthly totals, and every missing run reconcile exactly.",
                 "No values were imputed. These are data-quality exceptions, not model anomaly alerts.",
                 "See docs/data_quality_audit.md for definitions, limits, and the proposed missing-data policy.", ""]
        (output / "audit_summary.md").write_text("\n".join(lines), encoding="utf-8")
        print(json.dumps({"counts": counts, "exception_records": exception_count,
                          "reconciliation": summary["reconciliation"], "output_dir": str(output)}, indent=2))
        return summary
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("household_power_consumption.txt"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    audit(args.input, args.output_dir)


if __name__ == "__main__":
    main()
