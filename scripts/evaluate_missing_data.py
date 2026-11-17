"""Paired, stratified masking experiment; never rewrites source measurements."""
from __future__ import annotations

import argparse
from bisect import bisect_left, insort
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from scripts.audit_data_quality import FIELDS, HEADER, file_hash
except ModuleNotFoundError:
    from audit_data_quality import FIELDS, HEADER, file_hash

GAPS = (1, 3, 5, 10, 30)
WINDOW = max(GAPS) + 2
SEASONS = ("Winter", "Spring", "Summer", "Fall")
PERIODS = ("Night", "Morning", "Afternoon", "Evening")
METHODS = ("time_linear", "forward_fill")


def eligible_starts(values, times, window=WINDOW):
    """All points and internal one-minute steps must be valid; no sorting/filling."""
    if len(times) < window:
        return np.array([], dtype=int)
    timestamps = times.to_numpy(dtype="datetime64[ns]")
    point_valid = np.isfinite(values).all(axis=1) & (values >= 0).all(axis=1)
    point_valid &= ~np.isnat(timestamps)
    point_valid &= timestamps == timestamps.astype("datetime64[m]")
    bad_points = np.r_[0, np.cumsum(~point_valid)]
    breaks = np.r_[True, np.diff(timestamps) != np.timedelta64(1, "m")]
    bad_steps = np.r_[0, np.cumsum(breaks)]
    starts = np.arange(len(times) - window + 1)
    valid = (bad_points[starts + window] == bad_points[starts])
    valid &= bad_steps[starts + window] == bad_steps[starts + 1]
    return starts[valid]


def sample_nonoverlapping(candidates, count, rng, window=WINDOW):
    selected = []
    for start in rng.permutation(candidates):
        start = int(start)
        position = bisect_left(selected, start)
        if position and start - selected[position - 1] < window:
            continue
        if position < len(selected) and selected[position] - start < window:
            continue
        insort(selected, start)
        if len(selected) == count:
            return np.asarray(selected)
    raise ValueError(f"Only {len(selected)} nonoverlapping windows available; requested {count}")


def predictions(blocks, gap):
    """Regular-minute time interpolation: elapsed/(gap+1) minutes between endpoints."""
    if not 1 <= gap <= blocks.shape[1] - 2:
        raise ValueError("Gap needs a valid endpoint on each side")
    truth = blocks[:, 1:gap + 1, :]
    left = blocks[:, :1, :]
    right = blocks[:, gap + 1:gap + 2, :]
    elapsed_minutes = np.arange(1, gap + 1)[None, :, None]
    linear = left + (right - left) * elapsed_minutes / (gap + 1)
    forward = np.broadcast_to(left, truth.shape)
    return truth, linear, forward


def paired_bootstrap(differences, strata, repetitions, rng):
    """Resample paired windows within season/time strata, never individual minutes."""
    boot = np.zeros((repetitions, differences.shape[1]))
    labels = np.unique(strata)
    for label in labels:
        group = differences[strata == label]
        indices = rng.integers(0, len(group), size=(repetitions, len(group)))
        boot += group[indices].mean(axis=1) / len(labels)
    return np.quantile(boot, [0.025, 0.975], axis=0)


def make_chart(metrics, baseline, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(FIELDS), 2, figsize=(13, 22), constrained_layout=True)
    colors = {"time_linear": "#2166ac", "forward_fill": "#b35806"}
    for index, field in enumerate(FIELDS):
        reference = baseline[(baseline.partition == "holdout") & (baseline.measurement == field)].iloc[0]
        for col, metric in enumerate(("mae", "rmse")):
            ax = axes[index, col]
            for method in METHODS:
                subset = metrics[(metrics.partition == "holdout") & (metrics.measurement == field) & (metrics.method == method)]
                ax.plot(subset.gap_minutes, subset[metric], marker="o", color=colors[method],
                        label=method.replace("_", " "))
            ax.set_title(f"{field}\nObserved SD={reference.observed_sd:.4g}; mean |1-min change|={reference.mean_abs_minute_change:.4g}", fontsize=10)
            ax.set_xticks(GAPS)
            ax.set_xlabel("Hidden consecutive minutes")
            ax.set_ylabel(f"{metric.upper()} (field's native units)")
            ax.set_ylim(bottom=0)
            ax.grid(alpha=0.25)
            if index == 0:
                ax.legend()
    count = int(metrics[metrics.partition == "holdout"].n_windows.iloc[0])
    fig.suptitle(f"2010 holdout: time interpolation vs forward fill\n{count:,} matched windows per gap; balanced seasons/times of day; separate scales by field\nInterpolation uses the next observed value: retrospective cleaning only", fontsize=13)
    for extension in ("png", "svg"):
        fig.savefig(output / f"comparison.{extension}", dpi=160)
    plt.close(fig)


def evaluate(source, output, per_stratum=200, seed=20261117, split="2010-01-01", bootstrap=1000):
    if per_stratum < 2 or bootstrap < 100:
        raise ValueError("Use at least two windows per stratum and 100 bootstrap repetitions")
    source, output = source.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_hash = file_hash(source)
    notebook = Path(__file__).resolve().parents[1] / "final.ipynb"
    notebook_hash = file_hash(notebook)
    frame = pd.read_csv(source, sep=";", dtype=str, keep_default_na=False)
    if list(frame.columns) != HEADER:
        raise ValueError("Unexpected source schema")
    times = pd.to_datetime(frame.Date + " " + frame.Time, format="%d/%m/%Y %H:%M:%S", errors="coerce")
    values = frame[list(FIELDS)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    del frame
    starts = eligible_starts(values, times)
    months = times.dt.month.to_numpy()
    seasons = np.select([np.isin(months, [12, 1, 2]), np.isin(months, [3, 4, 5]),
                         np.isin(months, [6, 7, 8])], ["Winter", "Spring", "Summer"], default="Fall")
    hours = times.dt.hour.fillna(0).to_numpy(dtype=int)
    periods = np.asarray(PERIODS)[hours // 6]
    partitions = np.where(times < pd.Timestamp(split), "development", "holdout")
    # Keep both endpoints and every point in the same season/time/partition.
    same_group = ((seasons[starts] == seasons[starts + WINDOW - 1]) &
                  (periods[starts] == periods[starts + WINDOW - 1]) &
                  (partitions[starts] == partitions[starts + WINDOW - 1]))
    starts = starts[same_group]
    selection_rng = np.random.default_rng(seed)
    bootstrap_rng = np.random.default_rng(seed + 1)
    manifest_rows, eligibility_rows = [], []
    selected_by_partition = {}
    for partition in ("development", "holdout"):
        selected, labels = [], []
        for season in SEASONS:
            for period in PERIODS:
                candidates = starts[(partitions[starts] == partition) &
                                    (seasons[starts] == season) & (periods[starts] == period)]
                chosen = sample_nonoverlapping(candidates, per_stratum, selection_rng)
                eligibility_rows.append(dict(partition=partition, season=season, time_of_day=period,
                                             eligible_overlapping_starts=len(candidates), selected_windows=len(chosen)))
                for start in chosen:
                    window_id = f"{partition}_{season}_{period}_{len(manifest_rows) + 1:05d}"
                    manifest_rows.append(dict(window_id=window_id, partition=partition, season=season,
                                              time_of_day=period, source_start_row=int(start + 1),
                                              window_start=str(times.iloc[start]),
                                              window_end=str(times.iloc[start + WINDOW - 1])))
                selected.extend(chosen)
                labels.extend([season + "/" + period] * len(chosen))
        selected_by_partition[partition] = (np.asarray(selected), np.asarray(labels))
    manifest = pd.DataFrame(manifest_rows)
    manifest.to_csv(output / "selected_windows.csv", index=False)
    pd.DataFrame(eligibility_rows).to_csv(output / "sampling_counts.csv", index=False)
    # Retain all original values for the sampled 32-minute windows, including endpoints.
    truth_parts, masks, baseline_rows, metric_rows, stratum_rows, comparison_rows = [], [], [], [], [], []
    for partition, (selected, labels) in selected_by_partition.items():
        subset_manifest = manifest[manifest.partition == partition]
        indices = selected[:, None] + np.arange(WINDOW)[None, :]
        blocks = values[indices]
        flat = blocks.reshape(-1, len(FIELDS))
        baseline_sd = flat.std(axis=0, ddof=1)
        changes = np.abs(np.diff(blocks, axis=1)).reshape(-1, len(FIELDS))
        mean_change = changes.mean(axis=0)
        for field_index, field in enumerate(FIELDS):
            q25, q75 = np.quantile(flat[:, field_index], [0.25, 0.75])
            baseline_rows.append(dict(partition=partition, measurement=field,
                                      reference_points=len(flat), observed_sd=baseline_sd[field_index],
                                      observed_iqr=q75 - q25, mean_abs_minute_change=mean_change[field_index],
                                      median_abs_minute_change=np.median(changes[:, field_index]),
                                      minute_change_pairs=len(changes)))
        truth_frame = pd.DataFrame(flat, columns=FIELDS)
        truth_frame.insert(0, "timestamp", times.iloc[indices.ravel()].to_numpy())
        truth_frame.insert(0, "source_row_id", indices.ravel() + 1)
        truth_frame.insert(0, "minute_offset", np.tile(np.arange(WINDOW), len(selected)))
        truth_frame.insert(0, "window_id", np.repeat(subset_manifest.window_id.to_numpy(), WINDOW))
        truth_parts.append(truth_frame)
        for gap in GAPS:
            truth, linear, forward = predictions(blocks, gap)
            for window_id, start in zip(subset_manifest.window_id, selected):
                masks.append(dict(window_id=window_id, partition=partition, gap_minutes=gap,
                                  hidden_start=str(times.iloc[start + 1]), hidden_end=str(times.iloc[start + gap]),
                                  left_endpoint=str(times.iloc[start]), right_endpoint=str(times.iloc[start + gap + 1]),
                                  hidden_first_source_row=int(start + 2), hidden_last_source_row=int(start + gap + 1)))
            absolute_by_method = []
            for method, prediction in zip(METHODS, (linear, forward)):
                errors = prediction - truth
                absolute = np.abs(errors)
                absolute_by_method.append(absolute.mean(axis=1))
                for label in [None, *np.unique(labels)]:
                    group_errors = errors if label is None else errors[labels == label]
                    point_errors = group_errors.reshape(-1, len(FIELDS))
                    for field_index, field in enumerate(FIELDS):
                        e = point_errors[:, field_index]
                        mae, rmse = np.abs(e).mean(), np.sqrt(np.square(e).mean())
                        row = dict(partition=partition, gap_minutes=gap, measurement=field, method=method,
                                   n_windows=len(group_errors), n_masked_readings=len(e), mae=mae, rmse=rmse,
                                   p95_abs_error=np.quantile(np.abs(e), 0.95), p99_abs_error=np.quantile(np.abs(e), 0.99),
                                   max_abs_error=np.abs(e).max(), mean_error=e.mean(),
                                   reference_sd=baseline_sd[field_index],
                                   mae_over_sd=mae / baseline_sd[field_index] if baseline_sd[field_index] else np.nan,
                                   rmse_over_sd=rmse / baseline_sd[field_index] if baseline_sd[field_index] else np.nan,
                                   reference_mean_abs_minute_change=mean_change[field_index],
                                   mae_over_mean_abs_minute_change=mae / mean_change[field_index] if mean_change[field_index] else np.nan)
                        if label is None:
                            metric_rows.append(row)
                        else:
                            row["season"], row["time_of_day"] = label.split("/")
                            stratum_rows.append(row)
            differences = absolute_by_method[0] - absolute_by_method[1]
            bounds = paired_bootstrap(differences, labels, bootstrap, bootstrap_rng)
            for field_index, field in enumerate(FIELDS):
                comparison_rows.append(dict(partition=partition, gap_minutes=gap, measurement=field,
                                            paired_mae_difference_linear_minus_ffill=differences[:, field_index].mean(),
                                            ci95_low=bounds[0, field_index], ci95_high=bounds[1, field_index],
                                            n_paired_windows=len(blocks), bootstrap_repetitions=bootstrap))
        print(f"Evaluated {partition}: {len(selected):,} matched windows", flush=True)
    pd.concat(truth_parts, ignore_index=True).to_csv(output / "sampled_truth.csv", index=False)
    pd.DataFrame(masks).to_csv(output / "mask_manifest.csv", index=False)
    metrics, baseline = pd.DataFrame(metric_rows), pd.DataFrame(baseline_rows)
    metrics.to_csv(output / "metrics.csv", index=False)
    pd.DataFrame(stratum_rows).to_csv(output / "metrics_by_stratum.csv", index=False)
    baseline.to_csv(output / "variability_reference.csv", index=False)
    pd.DataFrame(comparison_rows).to_csv(output / "paired_method_comparison.csv", index=False)
    make_chart(metrics, baseline, output)
    if file_hash(source) != source_hash or file_hash(notebook) != notebook_hash:
        raise RuntimeError("Original source/notebook changed during evaluation")
    metadata = dict(source_file=source.name, source_sha256=source_hash, notebook_sha256=notebook_hash,
                    script_sha256=file_hash(Path(__file__)), seed=seed, holdout_start=split,
                    gap_minutes=GAPS, window_minutes=WINDOW, windows_per_season_time_stratum=per_stratum,
                    windows_per_partition=per_stratum * 16, bootstrap_repetitions=bootstrap,
                    bootstrap_unit="paired window within season/time-of-day strata",
                    normal_variability_reference="All 32 observed points in the same sampled windows, separate by partition and field",
                    dependencies=dict(python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__),
                    originals_unchanged=True,
                    limitation="Observed periods do not identify real outage behavior; linear interpolation uses a future endpoint.")
    (output / "experiment_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Reports and chart: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("household_power_consumption.txt"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/missing_data_evaluation"))
    parser.add_argument("--windows-per-stratum", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261117)
    parser.add_argument("--holdout-start", default="2010-01-01")
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    evaluate(args.input, args.output_dir, args.windows_per_stratum, args.seed, args.holdout_start, args.bootstrap)


if __name__ == "__main__":
    main()
