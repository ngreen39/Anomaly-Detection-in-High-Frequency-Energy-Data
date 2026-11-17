# Data-quality audit and proposed missing-data policy

This audit describes the source data. It does not impute values, modify
`final.ipynb`, train a model, or produce model anomaly alerts. All tools used are
free: Python's standard library and its bundled SQLite library.

The policy below preserves the original proposal. Its later, optional
implementation and observed-only modeling comparison are documented in the
[revised analysis](revised_analysis.md); the audit reports remain unchanged.

## Reproduce

From the project root, use Python 3.10+ with SQLite 3.25+ (for window functions):

```sh
python3 --version
python3 -c 'import sqlite3; print(sqlite3.sqlite_version)'
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -p 'test_audit_data_quality.py' -v
python3 scripts/audit_data_quality.py --input household_power_consumption.txt --output-dir reports
```

`requirements.txt` now installs the free local project dependencies, including
notebook, masking, and MySQL tools. The audit itself and its fixture tests still
use only the standard library; the pip step is optional for the audit alone.
The source must be the extracted semicolon-delimited file. If only the original
archive is available, extract it first. Reports are regenerated in the specified
directory; use another `--output-dir` to retain an earlier audit.

The audit stops on an incorrect header or a record with the wrong number of
fields rather than silently skipping malformed source records. A failed run may
leave partial reports; accept reports only after a successful exit and all three
reconciliation flags in `audit_summary.json` are true.

## Outputs and definitions

| File in `reports/` | Content |
| --- | --- |
| `audit_summary.md` | Concise counts and reconciliation result |
| `audit_summary.json` | Counts, per-field checks, source/query hashes, reconciliation details |
| `missing_runs.csv` | Chronological run ID, start/end, number of missing readings, duration in minutes |
| `exceptions.csv` | Source row ID, timestamp, field, rule, exact observed string, applicable run ID |
| `missingness_by_month.csv` | Source rows, missing measurement rows/cells, and absent timestamp rows separately |
| `longest_missing_runs.csv` | Ten longest missing runs, from SQL |
| `audit.sqlite3` | Derived row-level audit facts, exceptions, absent timestamps, and SQL-derived runs |

The SQLite database is ignored by Git and can be regenerated. The CSV files and
summaries are reviewable evidence; the source remains authoritative.

- `source_row_id` is a one-based **data record** number, excluding the header.
  In this dataset, source row 1 corresponds to text-file line 2. It is not a
  timestamp, so duplicate timestamps retain separate identifiers.
- A duplicate row is an exact repeat of all nine parsed source strings; each
  repeat after the first is flagged. Duplicate timestamps compare parsed values,
  even if their measurements differ. Records are never dropped by the audit.
- Timestamps use `%d/%m/%Y %H:%M:%S`, with no timezone conversion. Off-minute
  readings (nonzero seconds) are reported separately. Expected minutes run from
  the earliest through the latest valid, minute-aligned timestamp, inclusively.
  The audit cannot establish missing coverage before/after those bounds or infer
  daylight-saving conventions from this timezone-free file.
- An **absent timestamp row** is a minute in that grid with no on-grid source
  record. It has no source row identifier or observed value in `exceptions.csv`.
  It is never counted as an existing row with missing measurements.
- A **missing measurement row** is an existing record with at least one of seven
  measurement fields equal to `?` or empty after whitespace trimming. Each
  missing cell generates an exception. Exact observed strings are retained.
- Other unparseable numeric strings and NaN tokens are `invalid_numeric`;
  positive/negative infinity (including float overflow such as `1e999`) are
  `infinite_value`; finite negatives are `negative_value`. These categories are
  separate from missingness and from one another. Zero is not automatically
  invalid. This audit does not establish engineering limits or confirm faults.
- A **missing run** groups consecutive distinct, valid, minute-aligned
  timestamps with at least one missing measurement. These are row-level runs,
  not independent runs for each field. Duplicates do not extend a run. Absent
  minutes break a run; invalid numeric values are not missing tokens. Rows with
  missing measurements but invalid/off-grid timestamps remain exceptions and
  are counted under `missing_rows_without_grid_timestamp`.
- `number_of_missing_readings` counts **unique minute slots** in a run, not
  missing cells or duplicate source rows. `duration_minutes` is inclusive
  minute-slot coverage: `(end - start) / 1 minute + 1`. A one-reading run has
  duration one minute; its first-to-last elapsed time is zero minutes.
- Missing run IDs are chronological integers within one audit, not permanent
  identifiers across source revisions. They are assigned only to applicable
  `missing_measurement` exceptions. Multiple rules may flag the same source row.
- `exception_records` counts rule failures, not distinct bad records. There is
  no model-score column and no confirmed-fault classification.

The database retains row facts and exceptions, not a second full numeric copy of
the dataset. Original values remain in the unchanged source. Per-field missing
flags can be reconstructed by source row and field from `missing_measurement`
exceptions; any future cleaned table should retain those explicit flags.

## SQL evidence and reconciliation

Readable queries are in `sql/`. The audit executes them automatically:

1. `missingness_by_month.sql` counts missing rows/cells from `readings` and absent
   timestamps from a separate table. Unparseable dates use an
   `INVALID_TIMESTAMP` bucket rather than disappearing.
2. `missing_runs.sql` independently reconstructs runs from distinct missing
   minute points using a window function; Python separately groups sorted points.
3. `longest_missing_runs.sql` ranks the SQL-derived runs by duration.
4. `reconcile_totals.sql` aggregates stored audit facts and runs.

Python checks every SQL run (ID, start, end, count, duration), SQL aggregate totals,
and sums of monthly SQL results. Duplicate/numeric row flags are computed during
Python ingestion and aggregated by SQL; that aggregation is a reconciliation,
not a second independent numeric parser. Absent minutes are likewise identified
by Python and stored separately before SQL aggregation.

To inspect saved SQL against the generated database with built-in `sqlite3`:

```sh
python3 - <<'PY'
import sqlite3
from pathlib import Path

with sqlite3.connect('file:reports/audit.sqlite3?mode=ro', uri=True) as db:
    for name in ('missingness_by_month', 'longest_missing_runs', 'reconcile_totals'):
        print(name)
        cursor = db.execute(Path('sql', name + '.sql').read_text())
        print([column[0] for column in cursor.description])
        for row in cursor:
            print(row)
PY
```

## Proposed missing-data policy (not applied)

The real-data audit found 25,979 missing-measurement rows in 71 runs. Six runs
exceed a day, and the longest contains 7,226 minute readings. Those gaps do not
support interpolating all missing readings without a limit.

1. Preserve the raw file and row IDs. In any future derived table, retain original
   values, per-field `was_missing` and `was_invalid` flags, treatment method, and
   run ID. If an expected timestamp is inserted, flag it as `was_absent_timestamp`
   separately. Never replace an original value without retaining its provenance.
2. First resolve duplicate timestamps and invalid timestamps/values under an
   explicit rule; do not average conflicting duplicates silently. Validate
   cross-field constraints and units before defining physical plausibility limits.
3. Consider **at most five consecutive minutes** as an initial short-gap limit
   for retrospective interpolation, with valid observations immediately before
   and after each field's gap. Five minutes is a candidate to test, not a validated
   threshold: it limits smoothing in one-minute data while separating short
   interruptions from the observed multi-day gaps. Preserve every imputation flag.
   Assess absent timestamps separately before making them interpolation-eligible.
4. Test candidate limits of 1, 3, and 5 minutes by masking fully observed contiguous
   windows across months and times of day. Compare interpolation with carrying
   forward the last observation, measure per-field MAE and large errors, check
   peak suppression, and examine changes in model flags. Use temporal holdouts;
   derive choices on training data and report holdout results without claiming
   that masked windows prove how actual outages behaved. Select or reject the
   five-minute limit from those results and the intended analytic use.
5. Leave longer gaps and boundary gaps unimputed by default. Flag them for review;
   exclude affected rows/windows when a model requires complete observations.
   Exclude lagged windows crossing gaps. Report exclusion counts and coverage by
   month/season so filtering bias stays visible. Include observed-count coverage
   alongside daily/monthly aggregates and avoid treating missing power as zero.
6. Split chronologically before fitting preprocessing/model parameters. Two-sided
   interpolation uses future observations and is only appropriate for retrospective
   analysis; assess a causal treatment separately for live monitoring or forecasting.

The notebook's existing interpolation remains unchanged. Neither the audit nor
this proposed policy establishes meter faults, detection accuracy, financial
benefits, or time savings.
