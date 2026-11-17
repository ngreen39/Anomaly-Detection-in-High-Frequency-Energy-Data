# Revised notebook: execution and design

The current analysis is `revised_analysis_observed_baseline.ipynb`.
`final.ipynb` is the preserved historical notebook; it is not the source of the
revised performance claims. The raw measurement file is never overwritten.

## Reproduce with free local tools

Use Python 3.11+ (this run uses 3.13.5). Run from the project root:

```sh
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
python3 scripts/execute_revised_notebook.py
```

The runner creates a fresh Python kernel, executes every cell, and saves the
revised notebook's outputs only after successful completion. The execution
record is `reports/revised_analysis/notebook_execution.json`. No original notebook
is opened for writing. You can also open the revised notebook in Jupyter and
select Restart Kernel and Run All.

The notebook verifies completed audit, MySQL, and masking provenance and requires
their existing local report files. It does not connect to a database or read
credentials. MySQL queries are in `mysql/`; SQLite audit queries are in `sql/`.
The SQLite queries were restored byte-for-byte against the saved audit hashes
after the old paths were found missing. The MySQL scripts now follow the current
top-level `mysql/` layout. Prior audit/masking/MySQL report files are preserved.

Reports are written under `reports/revised_analysis/`. Re-running replaces only
those derived analysis reports and the revised notebook's executed outputs.

## Data and flags

Each scenario retains these separate in-memory tables:

- `original`: the original parsed measurements, never changed.
- `values`: measurements used by that analysis scenario.
- `was_missing`, `was_invalid`, `was_imputed`: one Boolean flag per source cell.
- Source row identifiers, timestamps, row retention flags, and missing run IDs.

Original text tokens remain in the unchanged raw file. The sparse
`measurement_policy_records.csv` exports every affected source record, including
all seven original/analysis values and per-field status flags. Source records not
listed have no missing/invalid measurements or invalid timestamps and all status
flags are False. This avoids duplicating millions of unaffected source records.

The primary scenario never imputes. The notebook explicitly enables an optional
sensitivity scenario using `RUN_RETROSPECTIVE_SENSITIVITY = True`; set it to
`False` to disable fills in that scenario as well.

The sensitivity policy accepts only **whole row-level runs of at most five
minutes**, fully bounded by valid, observed endpoints. It rejects leading/trailing
gaps, discontinuities, invalid/duplicate/off-grid timestamps, invalid endpoints,
and runs crossing the train/test boundary. Every accepted missing cell must be
filled and flagged. A longer run is never partially filled, even if one field has
a shorter missing segment within it. `missing_run_treatment.csv` records the
decision for every run. Longer gaps remain missing and excluded from complete-case
modeling. Valid observed cells, including internal anchors, are preserved.

The cap is **provisional and has not met field-specific acceptance tolerances**.
Interpolation uses a future endpoint; this is retrospective cleaning, not a live
forecasting claim. Masking observed windows does not establish how real missing
periods behaved.

## Model comparisons

The temporal split is January 1, 2010. Training samples come only from earlier
observed records; observed test targets come only from 2010. Retention counts in
`retention_counts.csv` describe the full source population before computational
training samples are selected. Actual training source IDs are exported separately.

Random Forest and its training-mean and intensity-linear baselines use the same
seeded sample of up to 60,000 observed training records. Sensitivity adds only
eligible imputed earlier-period records to that exact sample. Preprocessing is
inside training-fitted pipelines. Every regression metric uses the same observed
2010 test targets; imputed test targets are never scored against invented truth.
The task is **same-time active-power estimation**, using closely related meter
readings and calendar categories. It is not future prediction. Monthly holdout
metrics are exported to show variation across the available test period.

Isolation Forest uses up to 32,000 observed earlier-period training records plus
eligible earlier-period imputed records in the sensitivity fit. Both fits use the
same explicit hyperparameters and seed. Each fit's training-derived threshold is
used unchanged for both reported real holdout alerts and the synthetic proxy.
There is no separate high-contamination evaluation model. Thresholds differ
between fits because they are learned from different training sets; no threshold
is selected using proxy results. Alerts use the strict `score < threshold` rule.

The synthetic proxy uses 10,000 observed holdout records and 10,000 independently
perturbed copies. Synthetic recall is recovery of injected stress patterns only.
Proxy precision assumes original records are negative and depends on 50%
injection prevalence. Neither metric measures real fault detection. The exported
proxy records retain labels, feature values, scores, and thresholds for checking
the calculations.

`model_alerts.csv` contains only unlabeled holdout model alerts, with scenario,
source IDs, timestamps, scores, thresholds, decision scores, original-missing
status, missing-run IDs when applicable, and per-field imputation flags. Original
data-quality exceptions remain separately in `reports/exceptions.csv`. Unresolved
data-quality failures are excluded from scoring. A sensitivity alert on an
imputed record remains labeled as such; it is not an observed fault.

## Findings and limits

Read [measured findings](../reports/revised_analysis/findings.md),
[regression metrics](../reports/revised_analysis/regression_metrics.csv),
[Isolation Forest metrics](../reports/revised_analysis/isolation_forest_metrics.csv),
and [run metadata](../reports/revised_analysis/run_metadata.json).

Sensitivity changes include effects of tree resampling when training records are
added. They do not isolate a causal improvement from interpolation. A small
change in held-out error does not approve the policy. Other limits include serial
dependence within a single household, partial-year test coverage, physically
coupled same-time predictors, synthetic pattern/prevalence choices, absent domain
labels, and unestablished field-specific tolerances. No confirmed faults,
financial benefits, or time savings are claimed.
