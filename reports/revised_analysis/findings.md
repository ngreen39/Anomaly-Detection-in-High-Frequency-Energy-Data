# Revised analysis: measured findings

Primary baseline: observed-only records. Short-gap interpolation is a separate, provisional retrospective sensitivity analysis.

## Audit, SQL, and policy

The preserved audit has 2,075,259 source rows and 25,979 rows missing all seven measurements. No duplicate rows/timestamps, invalid timestamps, absent minutes, invalid numeric tokens, negatives, or infinities were found by those checks. These checks do not establish physical operating limits.
MySQL 9.4.0 independently reconstructed the runs and reconciled every monthly result, aggregate total, and exception count with the Python audit. This notebook verifies the saved source/query hashes and evidence; it does not require a live server or credentials.
The masking experiment favored interpolation over forward fill on aggregate at five minutes, but errors and subgroup differences remain. The provisional five-minute cap has NOT met field-specific acceptance tolerances. It is not approved as a universal cleaning rule.

Runs longer than five minutes are never partially filled. Leading/trailing, invalid-endpoint, noncontinuous, and split-crossing runs remain missing. Every filled cell has its own flag. Interpolation uses a future endpoint, so it is retrospective and does not establish live forecasting performance.

## Retention (source records, before model training samples)

| Scenario | Period | Retained | Imputed records | Imputed cells | Excluded |
| --- | --- | ---: | ---: | ---: | ---: |
| observed_only | all | 2,049,280 | 0 | 0 | 25,979 |
| observed_only | train_period | 1,591,886 | 0 | 0 | 8,350 |
| observed_only | test_period | 457,394 | 0 | 0 | 17,629 |
| short_gap_sensitivity | all | 2,049,356 | 76 | 532 | 25,903 |
| short_gap_sensitivity | train_period | 1,591,952 | 66 | 462 | 8,284 |
| short_gap_sensitivity | test_period | 457,404 | 10 | 70 | 17,619 |

## Same-time power estimation (observed 2010 targets only)

Earlier-period training samples are identical across scenarios; sensitivity adds eligible imputed earlier-period records. All preprocessing and each baseline are fitted on training records only. Sampling is for local compute; it is not a claim of full-population training. Calendar features use one-hot categories, not cyclical encoding.
The training-mean baseline is constant. The intensity-linear baseline learns a simple same-time relation from current intensity. Random Forest uses related simultaneous measurements and calendar features. Their physical relation to active power makes this estimation problem easier than forecasting. Imputed holdout targets are excluded from all regression metrics.

| Scenario | Estimator | Train rows (imputed) | Observed test targets | MAE | RMSE | R² |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| observed_only | training_mean | 60,000 (0) | 457,394 | 0.742154 | 0.933906 | -0.001933 |
| observed_only | intensity_linear | 60,000 (0) | 457,394 | 0.034893 | 0.049704 | 0.997162 |
| observed_only | random_forest | 60,000 (0) | 457,394 | 0.021528 | 0.036277 | 0.998488 |
| short_gap_sensitivity | training_mean | 60,066 (66) | 457,394 | 0.742285 | 0.933944 | -0.002015 |
| short_gap_sensitivity | intensity_linear | 60,066 (66) | 457,394 | 0.034897 | 0.049706 | 0.997162 |
| short_gap_sensitivity | random_forest | 60,066 (66) | 457,394 | 0.021543 | 0.036338 | 0.998483 |

MAE/RMSE use Global_active_power's native units. The holdout covers only available 2010 data through November 26; chronological separation does not remove serial dependence or establish generalization to other households.

## Isolation Forest: unlabeled alerts and synthetic proxy

Each scenario fits one earlier-period model with 150 trees, max_samples=256, contamination=0.02, and seed 42. Its learned score_samples threshold is frozen and used unchanged for both real holdout alerts and synthetic evaluation. A score strictly below the threshold is an alert. Thresholds may differ between scenario fits; none is tuned on the synthetic results.
Each proxy uses the same 10,000 observed 2010 records plus 10,000 modified copies (50% injection). Active power, intensity, voltage, and sub-metering 3 are perturbed independently. These are artificial stress patterns, not validated fault simulations. Original copies are assumed negative solely for computing proxy precision; their real labels are unknown.

| Scenario | Threshold | Real test rows | Unlabeled alerts (imputed) | Proxy alerts | Synthetic recall | Synthetic precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| observed_only | -0.652676430 | 457,394 | 4,854 (0) | 1,126 | 10.2400% | 90.9414% |
| short_gap_sensitivity | -0.656736847 | 457,404 | 5,579 (0) | 971 | 8.5500% | 88.0536% |

Synthetic precision depends on the chosen injection prevalence and assumed-negative originals; it is not real-world fault precision. Unlabeled real alerts are records for review, not confirmed faults, theft, or data-quality failures. Existing data-quality exceptions remain in ../exceptions.csv; model alerts are in model_alerts.csv. Unresolved measurement failures are never scored.

## Sensitivity and limits

```json
{
  "regression": {
    "common_observed_test_records": 457394,
    "mean_absolute_change_in_estimate": 0.002964712930333053,
    "max_absolute_change_in_estimate": 0.1043688879870115
  },
  "isolation_forest": {
    "common_observed_test_records": 457394,
    "alerts_in_both": 4368,
    "primary_only_alerts": 486,
    "sensitivity_only_alerts": 1211,
    "changed_alert_decisions": 1697
  }
}
```

Changes compare common observed test records. Adding a small number of imputed training records also changes tree sampling; differences cannot be attributed solely to the quality of imputed values. Neither tiny improvements nor degradations prove that imputation improves data quality. Imputed test rows have no observed targets and do not contribute accuracy evidence.
Masking observed periods cannot prove real missing-period behavior. No field-specific acceptance tolerances, domain fault labels, real alert precision, external-household validation, financial benefit, or time saving has been established. Keep long gaps missing and retain the observed-only baseline as primary.

## Evidence

Row-level links below open illustrative portfolio samples. Full exports stay local and are regenerated with the [documented commands](../../docs/portfolio_review.md). Metrics above use the full evaluation sets, never these samples.

- [Retention counts](retention_counts.csv), [missing-run treatment](missing_run_treatment.csv), [sparse original/value/flag provenance](../portfolio/samples/measurement_policy_records_sample.csv)
- [Regression metrics](regression_metrics.csv), [monthly metrics](regression_metrics_by_month.csv), [chart](regression_comparison.png)
- [Isolation metrics](isolation_forest_metrics.csv), [model alerts](../portfolio/samples/model_alerts_sample.csv), [synthetic proxy records](../portfolio/samples/synthetic_proxy_evaluation_sample.csv), [chart](isolation_comparison.png)
- [Training source IDs](../portfolio/samples/training_sample_ids_sample.csv), [run provenance](run_metadata.json)
- [Audit](../audit_summary.md), [MySQL reconciliation](../mysql/reconciliation.json), [masking results](../missing_data_evaluation/metrics.csv), [masking chart](../missing_data_evaluation/comparison.png)
