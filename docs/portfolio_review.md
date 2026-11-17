# Portfolio readiness and reproduction

Start with [the executed revised notebook](../revised_analysis_observed_baseline.ipynb).
[final.ipynb](../final.ipynb) is the preserved original. The review changes
presentation, documentation, and staging exclusions; no model or data-treatment
feature was added. Source data and both notebooks remain unchanged by this review.

## Claims checked against existing results

| Claim | Evidence |
| --- | --- |
| 2,075,259 source rows; 25,979 missing-measurement rows; 71 runs; no absent minute timestamps | [Audit summary](../reports/audit_summary.json) |
| MySQL matches Python totals, every monthly result, and every missing run | [Saved reconciliation](../reports/mysql/reconciliation.json) |
| 6,400 complete masking windows; separate errors by field and gap length | [Sampling counts](../reports/missing_data_evaluation/sampling_counts.csv), [metrics](../reports/missing_data_evaluation/metrics.csv) |
| Primary retains 2,049,280 observed rows; sensitivity adds 76 rows / 532 cells | [Retention counts](../reports/revised_analysis/retention_counts.csv), [run decisions](../reports/revised_analysis/missing_run_treatment.csv) |
| RF MAE 0.021528, RMSE 0.036277, R² 0.998488 on 457,394 observed holdout targets | [Regression results](../reports/revised_analysis/regression_metrics.csv) |
| Primary synthetic recall 10.24%, proxy precision 90.94%, 4,854 unlabeled alerts | [Isolation results](../reports/revised_analysis/isolation_forest_metrics.csv) |
| Same fitted model/threshold used for alerts and proxy within each scenario | [Analysis implementation](../scripts/revised_analysis.py), [run metadata](../reports/revised_analysis/run_metadata.json) |
| All nine code cells executed without errors in a fresh kernel | [Execution record](../reports/revised_analysis/notebook_execution.json) |

These are measured results, not business impact. Proxy precision assumes original
records are negative and uses 50% synthetic injection. The 10.24% recall shows
limited recovery of the chosen artificial patterns. Real records have no fault
labels. Same-time power estimation uses physically related simultaneous readings;
high R² does not establish future forecasting performance. The five-minute cap
remains provisional and has not met field-specific acceptance tolerances.

## Exact setup and dataset preparation

Run from the project root with Python 3.11+; the recorded run used 3.13.5.
All software is free. Package installation requires access to a package index;
the analysis itself uses local files only.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m ipykernel install --prefix "$PWD/.venv" --name python3 --display-name "Energy portfolio (.venv)"
export JUPYTER_PATH="$PWD/.venv/share/jupyter"
```

The kernel registration stays inside the ignored `.venv`. `JUPYTER_PATH` makes
the notebook runner use this environment's `python3` kernel for the current shell.

Both the 133 MB extracted raw file and the download ZIP are excluded from the
current repository tree. Use the official [UCI dataset page](https://archive.ics.uci.edu/dataset/235/individual+household+electric+power+consumption)
and [direct ZIP download](https://archive.ics.uci.edu/static/public/235/individual+household+electric+power+consumption.zip).
The download was verified on November 17, 2026: HTTP 200, with the extracted data
matching SHA-256 `4259c9d7ece5dbee9ab8d53682baac68d791c864f0f64a52b4043cb3b90894b7`.
Dataset attribution and license are in the README. Download and extract with:

```sh
python - <<'PYDATA'
from pathlib import Path
import hashlib, io, json, shutil, tempfile, urllib.request, zipfile

target = Path('household_power_consumption.txt')
expected = json.loads(Path('reports/audit_summary.json').read_text())['source_sha256']
url = 'https://archive.ics.uci.edu/static/public/235/individual+household+electric+power+consumption.zip'
if not target.exists():
    with urllib.request.urlopen(url, timeout=60) as response:
        archive_bytes = response.read()
    with tempfile.TemporaryDirectory(prefix='energy-dataset-') as temporary:
        extracted = Path(temporary) / target.name
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as bundle:
            with bundle.open(target.name) as incoming, extracted.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
        with extracted.open('rb') as handle:
            assert hashlib.file_digest(handle, 'sha256').hexdigest() == expected
        with extracted.open('rb') as incoming, target.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
with target.open('rb') as handle:
    assert hashlib.file_digest(handle, 'sha256').hexdigest() == expected
print('Dataset present and matches the completed audit; existing files were not overwritten.')
PYDATA
```

## Reproduce the revised analysis using saved evidence

Small audit, masking, and MySQL summary reports are included. A live database and
credentials are not needed to run the revised notebook:

```sh
python -m unittest discover -s tests -v
python scripts/execute_revised_notebook.py
```

The notebook runner starts a fresh local kernel. It overwrites only the revised
notebook's outputs and `reports/revised_analysis/`. Original data and `final.ipynb`
are preserved. To rebuild publication samples as well, use the complete block
below: the packaging step requires the full audit and masking exports, which a
source checkout intentionally omits. It then generates samples and the compact
chart and redirects the findings report's large-export links to those samples.

## Regenerate all ignored row-level exports

After setup and dataset preparation:

```sh
python scripts/audit_data_quality.py --input household_power_consumption.txt --output-dir reports
MPLCONFIGDIR=/tmp/energy-portfolio-mpl python scripts/evaluate_missing_data.py --input household_power_consumption.txt --output-dir reports/missing_data_evaluation --windows-per-stratum 200 --seed 20261117 --holdout-start 2010-01-01 --bootstrap 1000
python scripts/execute_revised_notebook.py
MPLCONFIGDIR=/tmp/energy-portfolio-mpl python scripts/prepare_portfolio_assets.py
```

The audit regenerates the SQLite database and full exception report. The masking
experiment regenerates selected windows, truth, and mask manifests. The revised
notebook regenerates full policy provenance, training IDs, predictions, proxy
records, and model alerts. Current metrics use those full evaluation sets, never
the portfolio samples. Runtime and final floating-point digits can vary by machine.

To rerun actual MySQL reconciliation rather than verify the saved evidence, follow
the [dedicated local MySQL instructions](mysql_setup.md). The isolated-instance
workflow is:

```sh
python scripts/local_mysql_server.py start --mysqld /usr/local/mysql/bin/mysqld
python scripts/reconcile_mysql.py --env-file .env --audit-dir reports --input household_power_consumption.txt --output-dir reports/mysql
python scripts/query_mysql.py reconcile_totals
python scripts/local_mysql_server.py stop
```

The binary path above is the installed macOS path used for validation; pass the
installed binary path on another system. Existing database names are refused.
For a subsequent import, first set `MYSQL_DATABASE` in private `.env` to a new
name beginning `energy_dq_audit_`. Never drop/reuse the existing audit database to
make the command succeed. `.env.example` has placeholders; do not commit `.env`.

## Publication files and staging review

Keep the notebooks, scripts, tests, dependency files, documentation, readable SQL,
small aggregate reports, charts, and [portfolio samples](../reports/portfolio/README.md).
[The sample manifest](../reports/portfolio/sample_manifest.json) records full/source
row counts, selection rules, seed, and file hashes. It is not an accuracy report.

The ignore rules exclude:

- `.env` variants except `.env.example`, the local MySQL data directory, database
  files, virtual environments, caches, and macOS metadata.
- The extracted raw dataset and nine unnecessarily large row-level exports.
  All full local files remain available; they have not been deleted or truncated.

Full stratified aggregate metrics and the full masking chart are retained because
they support the reported variation across fields, seasons, and times of day.
No new generated export over 1 MB needs staging. The original notebook remains
a historical artifact. The formerly tracked raw-data ZIP is removed from the
current tree now that an independently verified download replaces it.

Before staging, inspect candidates with:

```sh
git status --short
git diff --cached --stat
git ls-files --others --exclude-standard
git check-ignore .env .local-mysql/data .venv/lib household_power_consumption.txt reports/audit.sqlite3 reports/exceptions.csv
git diff --check
```

The raw-data ZIP deletion is intentional. Its replacement is the working UCI
download plus the non-overwriting extraction/hash-check commands above and in
the README. Only intended project files and this archive deletion are selected
for staging; local credentials, database files, environments, and full row-level
exports remain excluded.

## Remaining limits

No model- or notebook-execution failure is outstanding. Scientific limits remain:
unvalidated per-field interpolation tolerances, single-household/partial-year
coverage, serial dependence, synthetic stress-pattern assumptions, and unknown
real-alert precision. These are documented limits rather than performance claims.
The archive decision is resolved by the verified UCI download; it is no longer
a reproduction blocker.

## Review checks completed

- README figures were checked against full audit, masking, retention, and model
  result files; the three embedded charts use the existing results.
- All 23 tests passed. A separate temporary audit reproduced the saved audit
  summary byte-for-byte, preserving its MySQL provenance binding.
- Local documentation links were checked both for existence and for exclusion
  from Git, including the README's dataset-preparation anchor.
- Candidate files were scanned for the active local credential, private-key
  material, and recognizable AWS access-key patterns, without printing secret
  values. No matches were found. This is a focused review, not a guarantee
  against every possible secret format.
- Sample hashes, counts, paired scenarios, complete selected windows, and all
  532 retained imputation flags were checked. All full numeric exports and both
  notebooks are unchanged; the findings report changed only its publication
  links and sample explanation.
- The publication step requires explicit staging and staged-diff review.
  `git diff --check` and `git diff --cached --check` must be clean before committing.
