"""Observed-only modeling plus explicit, bounded retrospective sensitivity.

The original measurements and flags are separate from analysis values. No
notebook or source dataset is overwritten by these functions.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import IsolationForest, RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, precision_score, recall_score, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from scripts.audit_data_quality import FIELDS, HEADER, file_hash

SPLIT = pd.Timestamp("2010-01-01")
SEED = 42
SCENARIOS = ("observed_only", "short_gap_sensitivity")
IF_SETTINGS = dict(n_estimators=150, max_samples=256, contamination=0.02,
                   max_features=1.0, bootstrap=False, random_state=SEED, n_jobs=2)
RF_SETTINGS = dict(n_estimators=80, max_depth=20, random_state=SEED, n_jobs=2)
IF_FEATURES = [*FIELDS, "Is_Weekend", "Hour"]
RF_NUMERIC = list(FIELDS[1:])
RF_CALENDAR = ["Hour", "DayOfWeek", "Month", "Is_Weekend"]


@dataclass
class PolicyResult:
    original: pd.DataFrame
    values: pd.DataFrame
    timestamps: pd.Series
    was_missing: pd.DataFrame
    was_invalid: pd.DataFrame
    was_imputed: pd.DataFrame
    timestamp_valid: pd.Series
    retained: pd.Series
    run_ids: pd.Series
    runs: pd.DataFrame
    enabled: bool


def bounded_policy(original, timestamps, *, enabled=False, max_minutes=5, split=SPLIT):
    """Fill entire eligible row-level missing runs or none of a run.

    A run is consecutive source records with any missing measurement, matching
    the audit's row-level interpretation on this continuous, unique source.
    Its complete span and both endpoints must be valid consecutive minutes.
    All endpoints are fully observed. No run may cross the modeling split.
    Existing observed values inside a partial-field run are preserved and used
    as time interpolation anchors. Invalid values are never imputed.
    """
    if not 1 <= max_minutes <= 5:
        raise ValueError("The provisional sensitivity cap must be between 1 and 5 minutes")
    if list(original.columns) != list(FIELDS) or not original.index.equals(timestamps.index):
        raise ValueError("Measurement schema and timestamp index must match")
    missing, invalid, numeric = {}, {}, {}
    for field in FIELDS:
        source = original[field]
        absent = source.isna()
        if not pd.api.types.is_numeric_dtype(source):
            absent = absent | source.astype("string").str.strip().isin(["", "?"]).fillna(False)
        parsed = pd.to_numeric(source, errors="coerce").astype(float)
        bad = ~absent & (~np.isfinite(parsed) | (parsed < 0))
        missing[field], invalid[field] = absent, bad
        numeric[field] = parsed.mask(absent | bad)
    was_missing = pd.DataFrame(missing, index=original.index)
    was_invalid = pd.DataFrame(invalid, index=original.index)
    values = pd.DataFrame(numeric, index=original.index)
    was_imputed = pd.DataFrame(False, index=original.index, columns=FIELDS)
    valid_ts = (timestamps.notna() & timestamps.eq(timestamps.dt.floor("min")) &
                ~timestamps.duplicated(keep=False))
    row_missing = was_missing.any(axis=1).to_numpy()
    row_invalid = was_invalid.any(axis=1).to_numpy()
    fully_observed = ~row_missing & ~row_invalid & valid_ts.to_numpy()
    starts = np.flatnonzero(row_missing & ~np.r_[False, row_missing[:-1]])
    ends = np.flatnonzero(row_missing & ~np.r_[row_missing[1:], False])
    run_ids = pd.Series(pd.NA, index=original.index, dtype="Int64", name="missing_run_id")
    ledger = []
    for run_id, (start, end) in enumerate(zip(starts, ends), 1):
        size = end - start + 1
        run_ids.iloc[start:end + 1] = run_id
        if size > max_minutes:
            reason = "longer_than_cap"
        elif start == 0 or end == len(original) - 1:
            reason = "unbounded_leading_or_trailing"
        elif not (fully_observed[start - 1] and fully_observed[end + 1]):
            reason = "invalid_or_unobserved_endpoint"
        elif row_invalid[start:end + 1].any() or not valid_ts.iloc[start:end + 1].all():
            reason = "invalid_value_or_timestamp_in_run"
        else:
            local_times = timestamps.iloc[start - 1:end + 2]
            if not local_times.diff().iloc[1:].eq(pd.Timedelta(minutes=1)).all():
                reason = "noncontinuous_minutes"
            elif (local_times.iloc[0] < split) != (local_times.iloc[-1] < split):
                reason = "crosses_model_split"
            else:
                reason = "eligible"
        eligible = reason == "eligible"
        filled_count = 0
        if enabled and eligible:
            # Interpolate a complete, pre-approved bounded run; no limit= argument
            # that could partially fill the beginning of a longer run.
            local = values.iloc[start - 1:end + 2].copy()
            local.index = pd.DatetimeIndex(timestamps.iloc[start - 1:end + 2])
            proposed = local.interpolate(method="time", limit_area="inside").iloc[1:-1].to_numpy()
            mask = was_missing.iloc[start:end + 1].to_numpy()
            if not np.isfinite(proposed[mask]).all():
                raise AssertionError("An eligible complete run was not fully reconstructable")
            existing = values.iloc[start:end + 1].to_numpy(copy=True)
            existing[mask] = proposed[mask]
            values.iloc[start:end + 1] = existing
            was_imputed.iloc[start:end + 1] = mask
            filled_count = int(mask.sum())
        ledger.append(dict(missing_run_id=run_id, source_start_row=int(original.index[start]),
                           source_end_row=int(original.index[end]), start=timestamps.iloc[start],
                           end=timestamps.iloc[end], number_of_missing_readings=int(size),
                           duration_minutes=int(size), eligible=eligible, reason=reason,
                           imputed_values=filled_count))
    retained = valid_ts & ~was_invalid.any(axis=1) & values.notna().all(axis=1)
    if not enabled and was_imputed.to_numpy().any():
        raise AssertionError("Observed-only mode must not impute")
    # One flag for every changed missing value; never change a valid observed cell.
    if not was_imputed.equals(was_imputed & was_missing):
        raise AssertionError("Imputation occurred outside original missing cells")
    return PolicyResult(original, values, timestamps, was_missing, was_invalid, was_imputed,
                        valid_ts, retained, run_ids, pd.DataFrame(ledger), enabled)


def load_source(root):
    path = root / "household_power_consumption.txt"
    audit = json.loads((root / "reports/audit_summary.json").read_text())
    if file_hash(path) != audit["source_sha256"]:
        raise ValueError("Source does not match the completed audit")
    source = pd.read_csv(path, sep=";", na_values="?")
    if list(source.columns) != HEADER:
        raise ValueError("Unexpected source columns")
    source.index = pd.RangeIndex(1, len(source) + 1, name="source_row_id")
    timestamps = pd.to_datetime(source.Date + " " + source.Time, format="%d/%m/%Y %H:%M:%S", errors="coerce")
    original = source[list(FIELDS)].copy()
    return original, timestamps, audit


def retention_table(policies):
    rows = []
    for scenario, policy in policies.items():
        for partition, selection in [("all", pd.Series(True, index=policy.original.index)),
                                     ("train_period", policy.timestamps < SPLIT),
                                     ("test_period", policy.timestamps >= SPLIT)]:
            imputed = policy.was_imputed.any(axis=1)
            rows.append(dict(scenario=scenario, partition=partition,
                             source_records=int(selection.sum()),
                             retained_records=int((selection & policy.retained).sum()),
                             retained_observed_records=int((selection & policy.retained & ~imputed).sum()),
                             imputed_records=int((selection & imputed).sum()),
                             imputed_values=int(policy.was_imputed.loc[selection].sum().sum()),
                             excluded_records=int((selection & ~policy.retained).sum()),
                             original_missing_records=int((selection & policy.was_missing.any(axis=1)).sum()),
                             invalid_measurement_records=int((selection & policy.was_invalid.any(axis=1)).sum())))
    return pd.DataFrame(rows)


def export_policy(policy, output):
    """Sparse provenance: unaffected source rows have every status flag False."""
    affected = policy.was_missing.any(axis=1) | policy.was_invalid.any(axis=1) | ~policy.timestamp_valid
    selected = policy.original.index[affected]
    table = pd.DataFrame(index=selected)
    table["timestamp"] = policy.timestamps.loc[selected]
    table["missing_run_id"] = policy.run_ids.loc[selected]
    table["retained_in_sensitivity"] = policy.retained.loc[selected]
    for field in FIELDS:
        table[f"original__{field}"] = policy.original.loc[selected, field]
        table[f"analysis__{field}"] = policy.values.loc[selected, field]
        table[f"was_missing__{field}"] = policy.was_missing.loc[selected, field]
        table[f"was_invalid__{field}"] = policy.was_invalid.loc[selected, field]
        table[f"was_imputed__{field}"] = policy.was_imputed.loc[selected, field]
    table.to_csv(output / "measurement_policy_records.csv", index=True)
    policy.runs.to_csv(output / "missing_run_treatment.csv", index=False)


def features(policy):
    frame = policy.values.loc[policy.retained].copy()
    times = policy.timestamps.loc[frame.index]
    frame["Hour"] = times.dt.hour
    frame["DayOfWeek"] = times.dt.dayofweek
    frame["Month"] = times.dt.month
    frame["Is_Weekend"] = (times.dt.dayofweek >= 5).astype(int)
    return frame


def choose_training(observed_frame, timestamps, size):
    pool = observed_frame.index[timestamps.loc[observed_frame.index] < SPLIT]
    if not len(pool):
        raise ValueError("No earlier-period observed training records")
    return pd.Index(np.sort(np.random.default_rng(SEED).choice(pool.to_numpy(), size=min(size, len(pool)), replace=False)))


def training_ids_for_scenario(base_ids, policy):
    extra = policy.original.index[policy.was_imputed.any(axis=1) & policy.retained & (policy.timestamps < SPLIT)]
    return base_ids.append(extra).sort_values()


def regression_pipeline(kind):
    if kind == "training_mean":
        return Pipeline([("select", ColumnTransformer([("numeric", "passthrough", RF_NUMERIC)])),
                         ("model", DummyRegressor(strategy="mean"))])
    if kind == "intensity_linear":
        return Pipeline([("select", ColumnTransformer([("intensity", "passthrough", ["Global_intensity"])])),
                         ("model", LinearRegression())])
    if kind == "random_forest":
        preprocessing = ColumnTransformer([
            ("numeric", "passthrough", RF_NUMERIC),
            ("calendar", OneHotEncoder(handle_unknown="ignore", sparse_output=False), RF_CALENDAR),
        ])
        return Pipeline([("preprocess", preprocessing), ("model", RandomForestRegressor(**RF_SETTINGS))])
    raise ValueError(kind)


def metric_row(y, prediction):
    return dict(n_observed_targets=len(y), mae=float(mean_absolute_error(y, prediction)),
                rmse=float(np.sqrt(mean_squared_error(y, prediction))), r2=float(r2_score(y, prediction)))


def run_regression(policies, frames, output, train_size=60000):
    base_ids = choose_training(frames["observed_only"], policies["observed_only"].timestamps, train_size)
    times = policies["observed_only"].timestamps
    test_ids = frames["observed_only"].index[times.loc[frames["observed_only"].index] >= SPLIT]
    test = frames["observed_only"].loc[test_ids]
    rows, monthly, sampled, predictions, training = [], [], [], {}, []
    # Same evaluation source IDs in both scenarios; no imputed target is scored.
    display_ids = pd.Index(np.sort(np.random.default_rng(SEED).choice(test_ids, min(5000, len(test_ids)), replace=False)))
    for scenario in SCENARIOS:
        ids = training_ids_for_scenario(base_ids, policies[scenario])
        train = frames[scenario].loc[ids]
        training.extend(dict(algorithm="regression", scenario=scenario, source_row_id=int(i)) for i in ids)
        for kind in ("training_mean", "intensity_linear", "random_forest"):
            pipeline = regression_pipeline(kind)
            pipeline.fit(train, train.Global_active_power)
            prediction = pipeline.predict(test)
            rows.append(dict(scenario=scenario, estimator=kind, train_records=len(ids),
                             train_imputed_records=int(policies[scenario].was_imputed.loc[ids].any(axis=1).sum()),
                             **metric_row(test.Global_active_power, prediction)))
            months = times.loc[test_ids].dt.strftime("%Y-%m").to_numpy()
            for month in np.unique(months):
                mask = months == month
                monthly.append(dict(scenario=scenario, estimator=kind, month=month,
                                    **metric_row(test.Global_active_power.to_numpy()[mask], prediction[mask])))
            if kind == "random_forest":
                predictions[scenario] = pd.Series(prediction, index=test_ids)
                sample = pd.DataFrame({"source_row_id": display_ids, "timestamp": times.loc[display_ids].to_numpy(),
                                       "scenario": scenario, "observed_active_power": test.loc[display_ids, "Global_active_power"].to_numpy(),
                                       "estimated_active_power": predictions[scenario].loc[display_ids].to_numpy()})
                sampled.append(sample)
        print(f"Regression completed: {scenario} ({len(ids):,} train; {len(test):,} observed test targets)", flush=True)
    metrics = pd.DataFrame(rows)
    metrics.to_csv(output / "regression_metrics.csv", index=False)
    pd.DataFrame(monthly).to_csv(output / "regression_metrics_by_month.csv", index=False)
    pd.concat(sampled, ignore_index=True).to_csv(output / "regression_prediction_sample.csv", index=False)
    changes = predictions["short_gap_sensitivity"] - predictions["observed_only"]
    sensitivity = dict(common_observed_test_records=len(test_ids),
                       mean_absolute_change_in_estimate=float(changes.abs().mean()),
                       max_absolute_change_in_estimate=float(changes.abs().max()))
    return metrics, sensitivity, training


def anomaly_flags(scores, threshold):
    # Same strict comparison as IsolationForest.predict / decision_function < 0.
    return np.asarray(scores) < threshold


def synthetic_proxy(observed_test, n=10000):
    ids = pd.Index(np.sort(np.random.default_rng(SEED).choice(observed_test.index, min(n, len(observed_test)), replace=False)))
    original = observed_test.loc[ids, IF_FEATURES].copy()
    injected = original.copy()
    rng = np.random.default_rng(SEED + 1)
    for field, bounds in {"Global_active_power": (1.7, 2.8), "Global_intensity": (1.4, 2.3),
                          "Voltage": (0.84, 0.95), "Sub_metering_3": (1.3, 2.0)}.items():
        injected[field] *= rng.uniform(*bounds, len(injected))
    combined = pd.concat([original, injected], ignore_index=True)
    nominal = np.r_[np.zeros(len(original), dtype=int), np.ones(len(injected), dtype=int)]
    return ids, combined, nominal


def run_isolation(policies, frames, output, train_size=32000, proxy_size=10000):
    observed = frames["observed_only"]
    times = policies["observed_only"].timestamps
    base_ids = choose_training(observed, times, train_size)
    common_ids = observed.index[times.loc[observed.index] >= SPLIT]
    proxy_ids, proxy, nominal = synthetic_proxy(observed.loc[common_ids], n=proxy_size)
    alert_tables, evaluation_tables, metrics, training = [], [], [], []
    common_flags = {}
    for scenario in SCENARIOS:
        policy, frame = policies[scenario], frames[scenario]
        ids = training_ids_for_scenario(base_ids, policy)
        model = IsolationForest(**IF_SETTINGS).fit(frame.loc[ids, IF_FEATURES])
        threshold = float(model.offset_)
        training.extend(dict(algorithm="isolation_forest", scenario=scenario, source_row_id=int(i)) for i in ids)
        test_ids = frame.index[times.loc[frame.index] >= SPLIT]
        scores = pd.Series(model.score_samples(frame.loc[test_ids, IF_FEATURES]), index=test_ids)
        flags = pd.Series(anomaly_flags(scores, threshold), index=test_ids)
        common_flags[scenario] = flags.loc[common_ids]
        alert_ids = test_ids[flags.to_numpy()]
        alert_table = pd.DataFrame(index=alert_ids)
        alert_table.index.name = "source_row_id"
        alert_table["timestamp"] = times.loc[alert_ids]
        alert_table["scenario"] = scenario
        alert_table["score"] = scores.loc[alert_ids]
        alert_table["threshold"] = threshold
        alert_table["decision_score"] = scores.loc[alert_ids] - threshold
        alert_table["threshold_rule"] = "score < threshold"
        alert_table["was_imputed_any"] = policy.was_imputed.loc[alert_ids].any(axis=1)
        alert_table["was_missing_originally_any"] = policy.was_missing.loc[alert_ids].any(axis=1)
        alert_table["missing_run_id"] = policy.run_ids.loc[alert_ids]
        for field in FIELDS:
            alert_table[f"was_imputed__{field}"] = policy.was_imputed.loc[alert_ids, field]
        alert_tables.append(alert_table.reset_index())
        # Evaluate this fitted model and its EXACT alert threshold, with no retuning.
        proxy_scores = model.score_samples(proxy)
        proxy_flags = anomaly_flags(proxy_scores, threshold)
        if not np.array_equal(proxy_flags, model.predict(proxy) == -1):
            raise AssertionError("Proxy and real-alert threshold definitions differ")
        tp = int((proxy_flags & (nominal == 1)).sum())
        fp = int((proxy_flags & (nominal == 0)).sum())
        imputed_test = policy.was_imputed.loc[test_ids].any(axis=1)
        metrics.append(dict(scenario=scenario, train_records=len(ids),
                            train_imputed_records=int(policy.was_imputed.loc[ids].any(axis=1).sum()),
                            threshold=threshold, test_records=len(test_ids),
                            test_imputed_records=int(imputed_test.sum()),
                            real_alert_count=int(flags.sum()), common_observed_alert_count=int(flags.loc[common_ids].sum()),
                            common_observed_test_records=len(common_ids),
                            common_observed_alert_rate=float(flags.loc[common_ids].mean()),
                            imputed_test_alert_count=int((flags & imputed_test).sum()),
                            proxy_original_records=len(proxy_ids), proxy_injected_records=len(proxy_ids),
                            proxy_alert_count=int(proxy_flags.sum()), synthetic_true_positive_count=tp,
                            assumed_negative_alert_count=fp,
                            synthetic_recall=float(recall_score(nominal, proxy_flags, zero_division=0)),
                            synthetic_precision=float(precision_score(nominal, proxy_flags, zero_division=0))))
        proxy_export = proxy.copy()
        proxy_export.insert(0, "source_row_id", np.tile(proxy_ids.to_numpy(), 2))
        proxy_export.insert(1, "timestamp", np.tile(times.loc[proxy_ids].to_numpy(), 2))
        proxy_export.insert(2, "scenario", scenario)
        proxy_export["is_synthetic"] = nominal.astype(bool)
        proxy_export["nominal_proxy_label"] = nominal
        proxy_export["score"] = proxy_scores
        proxy_export["threshold"] = threshold
        proxy_export["alert"] = proxy_flags
        evaluation_tables.append(proxy_export)
        print(f"Isolation Forest completed: {scenario} ({int(flags.sum()):,} later-period unlabeled alerts)", flush=True)
    pd.concat(alert_tables, ignore_index=True).to_csv(output / "model_alerts.csv", index=False)
    pd.concat(evaluation_tables, ignore_index=True).to_csv(output / "synthetic_proxy_evaluation.csv", index=False)
    metrics = pd.DataFrame(metrics)
    metrics.to_csv(output / "isolation_forest_metrics.csv", index=False)
    primary, sensitivity = common_flags["observed_only"], common_flags["short_gap_sensitivity"]
    change = dict(common_observed_test_records=len(common_ids),
                  alerts_in_both=int((primary & sensitivity).sum()),
                  primary_only_alerts=int((primary & ~sensitivity).sum()),
                  sensitivity_only_alerts=int((~primary & sensitivity).sum()),
                  changed_alert_decisions=int((primary != sensitivity).sum()))
    return metrics, change, training


def plot_models(regression, isolation, output):
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    order = ["training_mean", "intensity_linear", "random_forest"]
    for ax, metric in zip(axes, ["mae", "rmse"]):
        for offset, scenario in enumerate(SCENARIOS):
            subset = regression[regression.scenario == scenario].set_index("estimator").loc[order]
            ax.bar(np.arange(3) + offset * 0.35, subset[metric], width=0.35, label=scenario)
        ax.set_xticks(np.arange(3) + 0.175, ["Training mean", "Intensity linear", "Random Forest"])
        ax.set_ylabel(f"{metric.upper()} — active power, native units")
        ax.set_title("Common observed 2010 targets")
    axes[0].legend(fontsize=8)
    fig.suptitle("Same-time estimation: chronological holdout, no imputed targets in metrics")
    fig.savefig(output / "regression_comparison.png", dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), constrained_layout=True)
    for ax, metric, title in zip(axes,
                                 ["common_observed_alert_rate", "synthetic_recall", "synthetic_precision"],
                                 ["Unlabeled observed holdout alert rate", "Synthetic recall", "Synthetic precision at 50% injection"]):
        ax.bar(["Observed-only", "Short-gap\nsensitivity"], isolation[metric] * 100, color=["#2166ac", "#b35806"])
        ax.set_ylabel("Percent")
        ax.set_title(title, fontsize=10)
    fig.suptitle("Each model's fixed training threshold is used for alerts and proxy evaluation")
    fig.savefig(output / "isolation_comparison.png", dpi=160)
    plt.close(fig)


def verify_existing_evidence(root):
    audit = json.loads((root / "reports/audit_summary.json").read_text())
    mysql = json.loads((root / "reports/mysql/reconciliation.json").read_text())
    masking = json.loads((root / "reports/missing_data_evaluation/experiment_metadata.json").read_text())
    if mysql["source_sha256"] != audit["source_sha256"] or masking["source_sha256"] != audit["source_sha256"]:
        raise ValueError("Existing evidence refers to different source data")
    if mysql["counts"] != audit["counts"] or not all(mysql["reconciliation"].values()):
        raise ValueError("MySQL evidence does not reconcile")
    if mysql["python_audit_summary_sha256"] != file_hash(root / "reports/audit_summary.json"):
        raise ValueError("MySQL evidence is not tied to this audit summary")
    for folder, hashes in [(root / "mysql", mysql["query_sha256"]),
                           (root / "sql", audit["sql_sha256"])]:
        if any(file_hash(folder / name) != digest for name, digest in hashes.items()):
            raise ValueError("A reconciled SQL query changed; review evidence before modeling")
    return audit, mysql, masking


def write_findings(root, retention, regression, isolation, sensitivity, provenance):
    output = root / "reports/revised_analysis"
    lines = ["# Revised analysis: measured findings", "",
             "Primary baseline: observed-only records. Short-gap interpolation is a separate, provisional retrospective sensitivity analysis.", "",
             "## Audit, SQL, and policy", "",
             "The preserved audit has 2,075,259 source rows and 25,979 rows missing all seven measurements. No duplicate rows/timestamps, invalid timestamps, absent minutes, invalid numeric tokens, negatives, or infinities were found by those checks. These checks do not establish physical operating limits.",
             "MySQL 9.4.0 independently reconstructed the runs and reconciled every monthly result, aggregate total, and exception count with the Python audit. This notebook verifies the saved source/query hashes and evidence; it does not require a live server or credentials.",
             "The masking experiment favored interpolation over forward fill on aggregate at five minutes, but errors and subgroup differences remain. The provisional five-minute cap has NOT met field-specific acceptance tolerances. It is not approved as a universal cleaning rule.", "",
             "Runs longer than five minutes are never partially filled. Leading/trailing, invalid-endpoint, noncontinuous, and split-crossing runs remain missing. Every filled cell has its own flag. Interpolation uses a future endpoint, so it is retrospective and does not establish live forecasting performance.", "",
             "## Retention (source records, before model training samples)", "",
             "| Scenario | Period | Retained | Imputed records | Imputed cells | Excluded |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for r in retention.itertuples():
        lines.append(f"| {r.scenario} | {r.partition} | {r.retained_records:,} | {r.imputed_records:,} | {r.imputed_values:,} | {r.excluded_records:,} |")
    lines += ["", "## Same-time power estimation (observed 2010 targets only)", "",
              "Earlier-period training samples are identical across scenarios; sensitivity adds eligible imputed earlier-period records. All preprocessing and each baseline are fitted on training records only. Sampling is for local compute; it is not a claim of full-population training. Calendar features use one-hot categories, not cyclical encoding.",
              "The training-mean baseline is constant. The intensity-linear baseline learns a simple same-time relation from current intensity. Random Forest uses related simultaneous measurements and calendar features. Their physical relation to active power makes this estimation problem easier than forecasting. Imputed holdout targets are excluded from all regression metrics.", "",
              "| Scenario | Estimator | Train rows (imputed) | Observed test targets | MAE | RMSE | R² |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for r in regression.itertuples():
        lines.append(f"| {r.scenario} | {r.estimator} | {r.train_records:,} ({r.train_imputed_records}) | {r.n_observed_targets:,} | {r.mae:.6f} | {r.rmse:.6f} | {r.r2:.6f} |")
    lines += ["", "MAE/RMSE use Global_active_power's native units. The holdout covers only available 2010 data through November 26; chronological separation does not remove serial dependence or establish generalization to other households.", "",
              "## Isolation Forest: unlabeled alerts and synthetic proxy", "",
              "Each scenario fits one earlier-period model with 150 trees, max_samples=256, contamination=0.02, and seed 42. Its learned score_samples threshold is frozen and used unchanged for both real holdout alerts and synthetic evaluation. A score strictly below the threshold is an alert. Thresholds may differ between scenario fits; none is tuned on the synthetic results.",
              "Each proxy uses the same 10,000 observed 2010 records plus 10,000 modified copies (50% injection). Active power, intensity, voltage, and sub-metering 3 are perturbed independently. These are artificial stress patterns, not validated fault simulations. Original copies are assumed negative solely for computing proxy precision; their real labels are unknown.", "",
              "| Scenario | Threshold | Real test rows | Unlabeled alerts (imputed) | Proxy alerts | Synthetic recall | Synthetic precision |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in isolation.itertuples():
        lines.append(f"| {r.scenario} | {r.threshold:.9f} | {r.test_records:,} | {r.real_alert_count:,} ({r.imputed_test_alert_count}) | {r.proxy_alert_count:,} | {r.synthetic_recall:.4%} | {r.synthetic_precision:.4%} |")
    lines += ["", "Synthetic precision depends on the chosen injection prevalence and assumed-negative originals; it is not real-world fault precision. Unlabeled real alerts are records for review, not confirmed faults, theft, or data-quality failures. Existing data-quality exceptions remain in ../exceptions.csv; model alerts are in model_alerts.csv. Unresolved measurement failures are never scored.", "",
              "## Sensitivity and limits", "", "```json", json.dumps(sensitivity, indent=2), "```", "",
              "Changes compare common observed test records. Adding a small number of imputed training records also changes tree sampling; differences cannot be attributed solely to the quality of imputed values. Neither tiny improvements nor degradations prove that imputation improves data quality. Imputed test rows have no observed targets and do not contribute accuracy evidence.",
              "Masking observed periods cannot prove real missing-period behavior. No field-specific acceptance tolerances, domain fault labels, real alert precision, external-household validation, financial benefit, or time saving has been established. Keep long gaps missing and retain the observed-only baseline as primary.", "",
              "## Evidence", "",
              "- [Retention counts](retention_counts.csv), [missing-run treatment](missing_run_treatment.csv), [sparse original/value/flag provenance](measurement_policy_records.csv)",
              "- [Regression metrics](regression_metrics.csv), [monthly metrics](regression_metrics_by_month.csv), [chart](regression_comparison.png)",
              "- [Isolation metrics](isolation_forest_metrics.csv), [model alerts](model_alerts.csv), [synthetic proxy records](synthetic_proxy_evaluation.csv), [chart](isolation_comparison.png)",
              "- [Training source IDs](training_sample_ids.csv), [run provenance](run_metadata.json)",
              "- [Audit](../audit_summary.md), [MySQL reconciliation](../mysql/reconciliation.json), [masking results](../missing_data_evaluation/metrics.csv), [masking chart](../missing_data_evaluation/comparison.png)", ""]
    (output / "findings.md").write_text("\n".join(lines))


def finalize(root, policies, retention, regression, isolation, sensitivity, training, original_hashes):
    output = root / "reports/revised_analysis"
    if any(file_hash(root / name) != digest for name, digest in original_hashes.items()):
        raise RuntimeError("An original file changed during analysis")
    pd.DataFrame(training).to_csv(output / "training_sample_ids.csv", index=False)
    for scenario, policy in policies.items():
        if not policy.original.equals(policies["observed_only"].original):
            raise AssertionError("Original measurements diverged between scenarios")
        observed_mask = ~policy.was_missing & ~policy.was_invalid
        original_numeric = policy.original.apply(pd.to_numeric, errors="coerce")
        if not np.array_equal(policy.values.to_numpy()[observed_mask.to_numpy()],
                              original_numeric.to_numpy()[observed_mask.to_numpy()]):
            raise AssertionError("An observed original measurement was changed")
    metadata = dict(source_sha256=original_hashes["household_power_consumption.txt"],
                    original_notebook_sha256=original_hashes["final.ipynb"],
                    analysis_code_sha256=file_hash(Path(__file__)), split=str(SPLIT), seed=SEED,
                    isolation_settings=IF_SETTINGS, regression_forest_settings=RF_SETTINGS,
                    regression_numeric_features=RF_NUMERIC, regression_calendar_features=RF_CALENDAR,
                    isolation_features=IF_FEATURES, primary="observed_only", sensitivity=sensitivity,
                    provisional_cap_minutes=5, field_specific_tolerances_met=False,
                    source_and_original_notebook_unchanged=True)
    (output / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    write_findings(root, retention, regression, isolation, sensitivity, metadata)
