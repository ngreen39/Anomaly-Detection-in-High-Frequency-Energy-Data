# Portfolio report samples

These files illustrate existing reports. **Published metrics use the full evaluation sets, never these samples.** Full exports remain local and are intentionally ignored by Git.

Run `python scripts/prepare_portfolio_assets.py` from the project root after regenerating the full exports. See [exact setup and regeneration commands](../../docs/portfolio_review.md).

| Sample | Kept rows | Full export rows | Selection |
| --- | ---: | ---: | --- |
| [exceptions_sample.csv](samples/exceptions_sample.csv) | 70 | 181,853 | Up to 10 per field/rule |
| [model_alerts_sample.csv](samples/model_alerts_sample.csv) | 40 | 10,433 | Up to 20 per scenario/imputation status; nonexistent alert groups are not invented |
| [measurement_policy_records_sample.csv](samples/measurement_policy_records_sample.csv) | 96 | 25,979 | All 76 imputed source records plus up to 20 unfilled records; complete imputed runs retained |
| [training_sample_ids_sample.csv](samples/training_sample_ids_sample.csv) | 80 | 184,132 | Up to 20 per algorithm/scenario |
| [synthetic_proxy_evaluation_sample.csv](samples/synthetic_proxy_evaluation_sample.csv) | 80 | 40,000 | 20 source IDs; keep all paired scenarios and synthetic/original copies for those IDs |
| [regression_prediction_sample.csv](samples/regression_prediction_sample.csv) | 40 | 10,000 | 20 source IDs; keep all paired scenarios and synthetic/original copies for those IDs |
| [selected_windows_sample.csv](samples/selected_windows_sample.csv) | 64 | 6,400 | Up to 2 complete windows per partition/season/time-of-day stratum |
| [sampled_truth_sample.csv](samples/sampled_truth_sample.csv) | 2,048 | 204,800 | Keep every truth point or mask scenario for the same selected complete windows |
| [mask_manifest_sample.csv](samples/mask_manifest_sample.csv) | 320 | 32,000 | Keep every truth point or mask scenario for the same selected complete windows |

Window selections retain all 32 observed points and all five mask lengths, with two windows per partition/season/time stratum. Proxy and prediction selections keep matching source IDs across scenarios. The policy sample retains all 76 imputed records (532 flags), plus 20 unfilled records; it does not truncate imputed runs.

Sampling uses seed 42 and is for inspection, not prevalence estimation. No missing rule, fault label, imputed alert, or performance result is invented to populate a group. All real alerts in this run were observed records, so the alert sample has no imputed-alert group.

The [manifest](sample_manifest.json) records hashes and counts. [The compact masking chart](missing_data_comparison.png) displays the existing per-field RMSE/SD ratios; it does not average errors across measurements.
