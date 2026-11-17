"""Load preserved audit facts into a NEW local MySQL database and reconcile SQL.

Credentials are read only from the ignored .env file, never from CLI arguments.
This script never drops a database/table and refuses to reuse an existing database.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3
import stat

try:
    from scripts.audit_data_quality import file_hash, write_csv
except ModuleNotFoundError:
    from audit_data_quality import file_hash, write_csv

SQL_DIR = Path(__file__).resolve().parents[1] / "mysql"
ALLOWED_KEYS = {"MYSQL_HOST", "MYSQL_PORT", "MYSQL_UNIX_SOCKET", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"}


def load_config(path):
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Credential file must be private: chmod 600 .env")
    config = {}
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or key not in ALLOWED_KEYS or key in config:
            raise ValueError(f"Invalid or duplicate .env key on line {number}")
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Unbalanced .env quotes on line {number}")
            value = value[1:-1]
        if "REPLACE_WITH_" in value or any(c in value for c in "\x00\r\n"):
            raise ValueError("Fill .env placeholders locally before connecting")
        config[key] = value
    if not {"MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"} <= config.keys():
        raise ValueError(".env requires MYSQL_USER, MYSQL_PASSWORD and MYSQL_DATABASE")
    if not config["MYSQL_USER"]:
        raise ValueError("MYSQL_USER must be explicit")
    if config.get("MYSQL_HOST", "127.0.0.1") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Only a local MySQL server is allowed")
    database = config["MYSQL_DATABASE"]
    if not re.fullmatch(r"energy_dq_audit_[A-Za-z0-9_]{1,48}", database):
        raise ValueError("Use a dedicated MYSQL_DATABASE starting with energy_dq_audit_ (max 64 characters)")
    port = int(config.get("MYSQL_PORT", "3306"))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid local MySQL port")
    socket = config.get("MYSQL_UNIX_SOCKET", "")
    if socket and not Path(socket).is_absolute():
        raise ValueError("MYSQL_UNIX_SOCKET must be absolute or empty")
    return dict(host=config.get("MYSQL_HOST", "127.0.0.1"), port=port,
                user=config["MYSQL_USER"], password=config["MYSQL_PASSWORD"],
                unix_socket=socket or None), database


def normalize(value):
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, Decimal):
        return int(value)
    return value


def check_server_version(version):
    match = re.match(r"(\d+)\.(\d+)\.", version)
    if not match or int(match[1]) < 8 or "mariadb" in version.lower():
        raise ValueError("MySQL 8.0+ is required for the supplied MySQL queries")


def create_database(cursor, database):
    # Identifier restricted by load_config; no IF NOT EXISTS and no DROP.
    if not re.fullmatch(r"energy_dq_audit_[A-Za-z0-9_]{1,48}", database):
        raise ValueError("Invalid dedicated database name")
    cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs")


def verify_mysql_queries(cursor):
    """Known fixture in our freshly created empty tables, rolled back afterward."""
    from datetime import timedelta
    epoch = datetime(1970, 1, 1)
    start = datetime(2020, 1, 1)
    minute = int((start - epoch).total_seconds()) // 60
    cursor.execute("SAVEPOINT known_query_fixture")
    try:
        def timestamp(offset):
            return start + timedelta(minutes=offset)
        cursor.executemany("INSERT INTO readings VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", [
            (1, timestamp(0), minute, 0, 0, 0, 0, 0, 0),
            (2, timestamp(1), minute + 1, 7, 0, 0, 0, 0, 0),
            (3, timestamp(1), minute + 1, 7, 0, 0, 0, 1, 1),
            (4, timestamp(2), minute + 2, 1, 0, 0, 0, 0, 0),
            (5, timestamp(4), minute + 4, 0, 0, 0, 0, 0, 0),
            (6, None, None, 0, 1, 1, 1, 0, 0),
        ])
        cursor.execute("INSERT INTO absent_timestamps VALUES (%s)", (timestamp(3),))
        cursor.execute((SQL_DIR / "missing_runs.sql").read_text())
        runs = cursor.fetchall()
        expected = ((1, timestamp(1), timestamp(2), 2, 2),)
        if tuple(runs) != expected:
            raise AssertionError("MySQL known missing-run fixture failed")
        cursor.executemany("INSERT INTO missing_runs VALUES (%s,%s,%s,%s,%s)", runs)
        cursor.execute((SQL_DIR / "reconcile_totals.sql").read_text())
        actual = tuple(normalize(v) for v in cursor.fetchone())
        if actual != (6, 1, 1, 1, 0, 3, 2, 15, 1, 1, 1, 1, 1, 2, 2):
            raise AssertionError("MySQL known totals fixture failed")
        cursor.execute((SQL_DIR / "missingness_by_month.sql").read_text())
        months = tuple(tuple(normalize(v) for v in row) for row in cursor.fetchall())
        if months != (("2020-01", 5, 3, 15, 1), ("INVALID_TIMESTAMP", 1, 0, 0, 0)):
            raise AssertionError("MySQL known monthly fixture failed")
        print("MySQL known fixture passed: duplicates, missing runs, absent minutes, invalid values", flush=True)
    finally:
        cursor.execute("ROLLBACK TO SAVEPOINT known_query_fixture")
        cursor.execute("RELEASE SAVEPOINT known_query_fixture")


def reconcile(env_file, audit_dir, source, output):
    import pymysql

    if output.resolve() == audit_dir.resolve():
        raise ValueError("Use a separate output directory to preserve Python audit reports")
    options, database = load_config(env_file)
    summary_path = audit_dir / "audit_summary.json"
    summary = json.loads(summary_path.read_text())
    if not all(summary["reconciliation"].values()):
        raise ValueError("The Python audit must reconcile successfully first")
    if file_hash(source) != summary["source_sha256"]:
        raise ValueError("Source hash differs from the preserved Python audit; rerun that audit first")
    sqlite_path = audit_dir / "audit.sqlite3"
    preserved = {path: file_hash(path) for path in audit_dir.glob("*.csv")}
    preserved[summary_path] = file_hash(summary_path)
    preserved[audit_dir / "audit_summary.md"] = file_hash(audit_dir / "audit_summary.md")
    preserved[sqlite_path] = file_hash(sqlite_path)
    sqlite_connection = sqlite3.connect(sqlite_path.resolve().as_uri() + "?mode=ro", uri=True)
    mysql = None
    created = False
    try:
        mysql = pymysql.connect(**options, charset="utf8mb4", autocommit=False,
                                connect_timeout=5, read_timeout=120, write_timeout=120)
        with mysql.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()[0]
            check_server_version(version)
            # A successful CREATE is the only route to schema/data changes.
            create_database(cursor, database)
            created = True
            mysql.select_db(database)
            for statement in (SQL_DIR / "schema.sql").read_text().split(";"):
                if statement.strip():
                    cursor.execute(statement)
            verify_mysql_queries(cursor)
            cursor.execute("INSERT INTO audit_metadata VALUES (%s,%s)", ("status", "import_in_progress"))
            cursor.execute("INSERT INTO audit_metadata VALUES (%s,%s)", ("source_sha256", summary["source_sha256"]))
            cursor.execute("INSERT INTO audit_metadata VALUES (%s,%s)", ("python_audit_summary_sha256", preserved[summary_path]))
            for table, query, insert in [
                ("readings", "SELECT * FROM readings ORDER BY source_row_id", "INSERT INTO readings VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"),
                ("exceptions", "SELECT * FROM exceptions", "INSERT INTO exceptions VALUES (%s,%s,%s,%s,%s)"),
                ("absent_timestamps", "SELECT * FROM absent_timestamps", "INSERT INTO absent_timestamps VALUES (%s)"),
            ]:
                source_cursor = sqlite_connection.execute(query)
                count = 0
                while batch := source_cursor.fetchmany(5000):
                    cursor.executemany(insert, batch)
                    count += len(batch)
                print(f"Imported {count:,} {table} audit facts into new dedicated database", flush=True)
            queries = {name: (SQL_DIR / f"{name}.sql").read_text() for name in
                       ("missing_runs", "missingness_by_month", "longest_missing_runs", "reconcile_totals")}
            cursor.execute(queries["missing_runs"])
            run_header = [column[0] for column in cursor.description]
            runs = [tuple(normalize(v) for v in row) for row in cursor.fetchall()]
            with (audit_dir / "missing_runs.csv").open(newline="") as handle:
                expected_runs = [tuple(row) for row in csv.reader(handle)][1:]
            if [tuple(map(str, row)) for row in runs] != expected_runs:
                raise AssertionError("MySQL missing runs differ from Python CSV")
            if runs:
                cursor.executemany("INSERT INTO missing_runs VALUES (%s,%s,%s,%s,%s)", runs)
            cursor.execute(queries["reconcile_totals"])
            totals = dict(zip([c[0] for c in cursor.description], [normalize(v) for v in cursor.fetchone()]))
            if totals != summary["counts"]:
                raise AssertionError("MySQL totals differ from Python audit")
            cursor.execute(queries["missingness_by_month"])
            month_header = [column[0] for column in cursor.description]
            months = [tuple(normalize(v) for v in row) for row in cursor.fetchall()]
            with (audit_dir / "missingness_by_month.csv").open(newline="") as handle:
                expected_months = [tuple(row) for row in csv.reader(handle)][1:]
            if [tuple(map(str, row)) for row in months] != expected_months:
                raise AssertionError("MySQL monthly results differ from Python audit CSV")
            cursor.execute(queries["longest_missing_runs"])
            longest = [tuple(normalize(v) for v in row) for row in cursor.fetchall()]
            cursor.execute("SELECT COUNT(*) FROM exceptions")
            exception_count = cursor.fetchone()[0]
            if exception_count != summary["exception_records"]:
                raise AssertionError("MySQL exception row count differs from Python")
            cursor.execute("UPDATE audit_metadata SET value_text=%s WHERE key_name=%s", ("reconciled", "status"))
            mysql.commit()
        # Verify the source reports were not altered by this adapter.
        if any(file_hash(path) != digest for path, digest in preserved.items()) or file_hash(source) != summary["source_sha256"]:
            raise RuntimeError("Source/audit artifacts changed during MySQL import")
        output.mkdir(parents=True, exist_ok=True)
        write_csv(output / "missing_runs.csv", run_header, runs)
        write_csv(output / "missingness_by_month.csv", month_header, months)
        write_csv(output / "longest_missing_runs.csv", run_header, longest)
        result = dict(database=database, mysql_version=version, source_sha256=summary["source_sha256"],
                      python_audit_summary_sha256=preserved[summary_path], counts=totals,
                      exception_records=exception_count,
                      reconciliation=dict(totals_match=True, every_run_matches=True, every_month_matches=True,
                                          exception_count_matches=True, python_artifacts_unchanged=True),
                      known_sql_fixture_passed=True,
                      query_sha256={path.name: file_hash(path) for path in SQL_DIR.glob("*.sql")})
        (output / "reconciliation.json").write_text(json.dumps(result, indent=2) + "\n")
        print(f"MySQL reconciliation passed. Reports: {output}", flush=True)
        return result
    except Exception:
        if mysql is not None:
            mysql.rollback()
        if created:
            print("Import failed. The new database is retained for inspection; no database was dropped. Use a fresh name for retry.")
        raise
    finally:
        sqlite_connection.close()
        if mysql is not None:
            mysql.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--audit-dir", type=Path, default=Path("reports"))
    parser.add_argument("--input", type=Path, default=Path("household_power_consumption.txt"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/mysql"))
    args = parser.parse_args()
    import pymysql
    try:
        reconcile(args.env_file, args.audit_dir, args.input, args.output_dir)
    except pymysql.Error as exc:
        code = exc.args[0] if exc.args else "unknown"
        if code == 1007:
            raise SystemExit("Configured database already exists; refused to reuse it. Choose a fresh MYSQL_DATABASE in .env.") from None
        raise SystemExit(f"Local MySQL operation failed (code {code}). Check credentials/privileges in .env; existing databases are never reused.") from None
    except (ValueError, FileNotFoundError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
