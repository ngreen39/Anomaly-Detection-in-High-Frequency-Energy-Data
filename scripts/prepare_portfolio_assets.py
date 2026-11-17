"""Package existing results for review: deterministic samples and one compact chart.

No audit, model, interpolation, or metric is changed or rerun. Full local CSVs
remain untouched. Samples are illustrations, not a basis for performance claims.
"""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SEED = 42


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def stratified(frame, columns, maximum):
    return pd.concat([group.sample(n=min(maximum, len(group)), random_state=SEED)
                      for _, group in frame.groupby(columns, sort=True, dropna=False)]).sort_index()


def main():
    output = ROOT / "reports/portfolio"
    samples = output / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    manifest = dict(seed=SEED, purpose="Illustrative samples only; metrics use full evaluation sets",
                    sources=[])

    def read(relative):
        return pd.read_csv(ROOT / relative, dtype=str, keep_default_na=False)

    def save(relative, frame, chosen, rule):
        name = Path(relative).stem.removesuffix("_sample") + "_sample.csv"
        target = samples / name
        chosen.to_csv(target, index=False)
        manifest["sources"].append(dict(source=relative, source_rows=len(frame), sample_rows=len(chosen),
                                         sample="samples/" + name, selection=rule,
                                         source_sha256=sha256(ROOT / relative), sample_sha256=sha256(target)))

    path = "reports/exceptions.csv"
    frame = read(path)
    save(path, frame, stratified(frame, ["field", "rule"], 10), "Up to 10 per field/rule")
    path = "reports/revised_analysis/model_alerts.csv"
    frame = read(path)
    save(path, frame, stratified(frame, ["scenario", "was_imputed_any"], 20),
         "Up to 20 per scenario/imputation status; nonexistent alert groups are not invented")
    path = "reports/revised_analysis/measurement_policy_records.csv"
    frame = read(path)
    imputed_columns = [c for c in frame if c.startswith("was_imputed__")]
    imputed = frame[imputed_columns].eq("True").any(axis=1)
    chosen = pd.concat([frame[imputed], frame[~imputed].sample(n=min(20, int((~imputed).sum())), random_state=SEED)]).sort_index()
    save(path, frame, chosen, "All 76 imputed source records plus up to 20 unfilled records; complete imputed runs retained")
    path = "reports/revised_analysis/training_sample_ids.csv"
    frame = read(path)
    save(path, frame, stratified(frame, ["algorithm", "scenario"], 20), "Up to 20 per algorithm/scenario")
    for path in ["reports/revised_analysis/synthetic_proxy_evaluation.csv",
                 "reports/revised_analysis/regression_prediction_sample.csv"]:
        frame = read(path)
        source_ids = pd.Series(frame.source_row_id.unique()).sample(n=min(20, frame.source_row_id.nunique()), random_state=SEED)
        chosen = frame[frame.source_row_id.isin(source_ids)]
        save(path, frame, chosen, "20 source IDs; keep all paired scenarios and synthetic/original copies for those IDs")
    windows_path = "reports/missing_data_evaluation/selected_windows.csv"
    windows = read(windows_path)
    selected = stratified(windows, ["partition", "season", "time_of_day"], 2)
    save(windows_path, windows, selected, "Up to 2 complete windows per partition/season/time-of-day stratum")
    for path in ["reports/missing_data_evaluation/sampled_truth.csv",
                 "reports/missing_data_evaluation/mask_manifest.csv"]:
        frame = read(path)
        save(path, frame, frame[frame.window_id.isin(selected.window_id)],
             "Keep every truth point or mask scenario for the same selected complete windows")

    # Presentation only: per-field RMSE divided by the existing field SD,
    # already recorded as rmse_over_sd. No raw error scores are pooled.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    metrics = pd.read_csv(ROOT / "reports/missing_data_evaluation/metrics.csv")
    subset = metrics[metrics.partition == "holdout"]
    fields = list(subset.measurement.drop_duplicates())
    gaps = [1, 3, 5, 10, 30]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8), constrained_layout=True, sharey=True)
    maximum = float(subset.rmse_over_sd.max() * 100)
    for ax, method, title in zip(axes, ["time_linear", "forward_fill"], ["Time interpolation", "Forward fill"]):
        matrix = subset[subset.method == method].pivot(index="measurement", columns="gap_minutes", values="rmse_over_sd").loc[fields, gaps].to_numpy() * 100
        artist = ax.imshow(matrix, cmap="YlOrRd", vmin=0, vmax=maximum, aspect="auto")
        ax.set_xticks(range(len(gaps)), gaps)
        ax.set_yticks(range(len(fields)), fields)
        ax.set_xlabel("Hidden consecutive minutes")
        ax.set_title(title)
        for row in range(len(fields)):
            for col in range(len(gaps)):
                ax.text(col, row, f"{matrix[row, col]:.0f}", ha="center", va="center",
                        color="white" if matrix[row, col] > maximum * 0.6 else "black")
    fig.colorbar(artist, ax=axes, label="RMSE / each field's observed SD (%) — lower is better", shrink=0.85)
    fig.suptitle("Observed-period masking: reconstruction error by measurement\n2010 holdout; 3,200 matched windows per gap; fields normalized separately", fontsize=12)
    fig.savefig(output / "missing_data_comparison.png", dpi=150)
    plt.close(fig)
    manifest["chart"] = dict(source="reports/missing_data_evaluation/metrics.csv",
                              source_sha256=sha256(ROOT / "reports/missing_data_evaluation/metrics.csv"),
                              transform="Existing rmse_over_sd × 100, separately for each field; no combined score")
    (output / "sample_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # Keep the generated findings report's links usable in a source checkout,
    # where the full row-level exports are intentionally ignored.
    findings = ROOT / "reports/revised_analysis/findings.md"
    text = findings.read_text()
    for entry in manifest["sources"]:
        if entry["source"].startswith("reports/revised_analysis/"):
            filename = Path(entry["source"]).name
            sample = "../portfolio/" + entry["sample"]
            text = text.replace(f"]({filename})", f"]({sample})")
    marker = "## Evidence\n"
    note = "\nRow-level links below open illustrative portfolio samples. Full exports stay local and are regenerated with the [documented commands](../../docs/portfolio_review.md). Metrics above use the full evaluation sets, never these samples.\n"
    if note.strip() not in text:
        text = text.replace(marker, marker + note)
    findings.write_text(text)
    print(f"Prepared {len(manifest['sources'])} report samples and compact masking chart in {output}")


if __name__ == "__main__":
    main()
