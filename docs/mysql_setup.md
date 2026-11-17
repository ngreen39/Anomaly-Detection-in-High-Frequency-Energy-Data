# Local MySQL audit analysis

The Python audit, original SQLite queries/database, and original CSV reports are
preserved. MySQL receives their row-level audit facts and independently computes
missing runs and monthly SQL aggregates. It does not impute measurements or
produce model anomaly alerts.

## Dependencies

Use Python 3.10+, a local MySQL Community Server 8.0+ installation, and the free
connector:

```sh
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -p 'test_reconcile_mysql.py' -v
```

`PyMySQL[rsa]` supports MySQL password authentication. The existing standard-library
audit does not gain a connector dependency. Readable MySQL SQL lives in
`mysql/`; SQLite SQL remains in `sql/`.

## Instance used for this validation

An isolated instance was initialized using the already installed
`/usr/local/mysql/bin/mysqld` (MySQL Community Server 9.4.0). Its data is in the
ignored `.local-mysql/` directory, and it listens only on a Unix socket in a
private temporary directory. Both classic TCP networking and MySQL X are
disabled. No system service configuration, existing server, or existing database
was changed.

The isolated instance was stopped after validation, with its database preserved.
Restart it with the command below before querying it.

The generated root password for **this isolated instance only** is in `.env`,
mode `0600`, along with the unique database name and socket. No credential is in
the command line, published report, or committed file. Keep `.env` and
`.local-mysql/` private and untracked. `.env.example` contains placeholders only.

Start the preserved instance and inspect existing results:

```sh
python3 scripts/local_mysql_server.py start
python3 scripts/query_mysql.py reconcile_totals
python3 scripts/query_mysql.py missingness_by_month
python3 scripts/query_mysql.py longest_missing_runs
```

Stop only this instance, preserving its database:

```sh
python3 scripts/local_mysql_server.py stop
```

The helper checks the connected server's data directory before shutdown. It does
not stop the system MySQL service. On first initialization it replaces `.env`
placeholders only; it refuses to overwrite a populated credential file or
initialize a nonempty existing state directory. The initial password-free
bootstrap occurs only inside the new private socket directory with networking
disabled; a random password is then assigned before importing audit data.

## Reproduce an import

If necessary, first generate the audit:

```sh
python3 scripts/audit_data_quality.py --input household_power_consumption.txt --output-dir reports
```

For a **new** MySQL import, edit `MYSQL_DATABASE` in `.env` to a fresh name starting
with `energy_dq_audit_`. The current name already exists after successful
validation. Never reuse it for importing. Then:

```sh
python3 scripts/local_mysql_server.py start
python3 scripts/reconcile_mysql.py --env-file .env --audit-dir reports --input household_power_consumption.txt --output-dir reports/mysql
```

The importer issues `CREATE DATABASE` **without** `IF NOT EXISTS`. If the database
exists, it fails before selecting it or changing its tables. There is no
database/table drop, truncation, or replacement path. A failed new import can
leave an incomplete new database; retain it for inspection and use another fresh
name when retrying. The standard MySQL installation uses InnoDB transactions.

The loader validates SQL on a known fixture in the new empty database, rolling
back fixture rows before loading the real facts. It verifies:

- Every Python audit count against MySQL aggregate results.
- Every missing run (ID, start, end, reading count, duration).
- Every monthly result, preserving the distinction between existing rows with
  missing measurements and absent timestamps.
- The imported exception count and hashes of the untouched Python artifacts.

Files in `reports/mysql/` are MySQL-derived copies:
`missing_runs.csv`, `missingness_by_month.csv`, `longest_missing_runs.csv`, and
`reconciliation.json`. The JSON records the actual database name, MySQL version,
source hash, query hashes, totals, and passed checks, but no credentials.

The loader hashes the source before importing and checks it against the Python
audit provenance. Numeric, duplicate, and absent-minute flags come from Python;
MySQL independently groups the imported minute facts into missing runs. These
checks reconcile audit results, not independent numeric parsing from the raw file.

## Use your usual local server instead

You can use a local account permitted to create a **new dedicated database** and
create/insert/select its tables. First stop the isolated instance if it is running,
then preserve its current `.env` privately if you want to reconnect to it later.
Copy `.env.example` to a private `.env` and set:

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_UNIX_SOCKET=
MYSQL_USER=REPLACE_WITH_LOCAL_USER
MYSQL_PASSWORD=REPLACE_WITH_LOCAL_PASSWORD
MYSQL_DATABASE=energy_dq_audit_new_unique_name
```

For a Unix socket connection, set `MYSQL_UNIX_SOCKET` to that server's absolute
socket path. TCP hosts are restricted to loopback. Run `chmod 600 .env` before the
importer. Values are literal strings with optional matching outer quotes; do not
source this file as shell code. No shell expansion, interpolation, or inline
comments are supported. Do not put credentials in command arguments or paste
them into logs. The importer refuses placeholder values.

The isolated-server helper intentionally refuses to operate on credentials that
point elsewhere. The importer and read-only query helper work with either local
server. MySQL Workbench can also open the saved `.sql` files against the dedicated
database using the matching local connection settings.
