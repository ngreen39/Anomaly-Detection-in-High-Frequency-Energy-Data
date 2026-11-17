"""Run one saved analysis query in a read-only transaction on the configured DB."""
import argparse
import csv
from pathlib import Path
import sys

try:
    from scripts.reconcile_mysql import SQL_DIR, load_config, normalize
except ModuleNotFoundError:
    from reconcile_mysql import SQL_DIR, load_config, normalize


def main():
    import pymysql

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", choices=("missingness_by_month", "missing_runs", "longest_missing_runs", "reconcile_totals"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    options, database = load_config(args.env_file)
    try:
        with pymysql.connect(**options, database=database, charset="utf8mb4", connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute((SQL_DIR / (args.query + ".sql")).read_text())
                writer = csv.writer(sys.stdout)
                writer.writerow([column[0] for column in cursor.description])
                writer.writerows(tuple(normalize(v) for v in row) for row in cursor.fetchall())
            connection.rollback()
    except pymysql.Error as exc:
        raise SystemExit(f"Local MySQL query failed (code {exc.args[0]}). Check that the isolated server is started.") from None


if __name__ == "__main__":
    main()
