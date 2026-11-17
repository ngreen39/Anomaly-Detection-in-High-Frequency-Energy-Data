import tempfile
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from scripts.audit_data_quality import FIELDS
from scripts.revised_analysis import (
    SPLIT, anomaly_flags, bounded_policy, features, regression_pipeline,
    run_isolation, run_regression, training_ids_for_scenario,
)


def fixture(size=12, start="2009-01-01"):
    index = pd.RangeIndex(1, size + 1, name="source_row_id")
    original = pd.DataFrame({field: np.arange(size, dtype=float) + i + 1
                             for i, field in enumerate(FIELDS)}, index=index)
    times = pd.Series(pd.date_range(start, periods=size, freq="min"), index=index)
    return original, times


class BoundedPolicyTests(unittest.TestCase):
    def test_optional_mode_preserves_original_and_flags_each_filled_cell(self):
        raw, times = fixture()
        raw.iloc[3:8] = np.nan  # A complete bounded five-minute run.
        before = raw.copy(deep=True)
        primary = bounded_policy(raw, times)
        sensitivity = bounded_policy(raw, times, enabled=True)
        pd.testing.assert_frame_equal(raw, before)
        pd.testing.assert_frame_equal(primary.values, before)
        self.assertEqual(primary.was_imputed.sum().sum(), 0)
        self.assertEqual(sensitivity.was_imputed.sum().sum(), 35)
        self.assertTrue(sensitivity.was_missing.iloc[3:8].to_numpy().all())
        self.assertTrue(sensitivity.was_imputed.iloc[3:8].to_numpy().all())
        np.testing.assert_allclose(sensitivity.values.iloc[3:8].to_numpy(),
                                   np.array([[j + i + 1 for i in range(7)] for j in range(3, 8)]))
        pd.testing.assert_frame_equal(sensitivity.values.iloc[:3], before.iloc[:3])

    def test_never_partially_fills_a_longer_run_even_for_one_short_field(self):
        raw, times = fixture(15)
        raw.iloc[3:9, 0] = np.nan  # Six missing minutes in field 0.
        raw.iloc[5, 1] = np.nan   # One missing minute inside the same row-level run.
        result = bounded_policy(raw, times, enabled=True)
        self.assertEqual(result.was_imputed.sum().sum(), 0)
        self.assertTrue(result.values.iloc[3:9, 0].isna().all())
        self.assertTrue(pd.isna(result.values.iloc[5, 1]))
        self.assertEqual(result.runs.iloc[0].reason, "longer_than_cap")

    def test_leading_trailing_and_invalid_endpoints_remain_missing(self):
        raw, times = fixture(14)
        raw.iloc[0:2] = np.nan
        raw.iloc[-2:] = np.nan
        raw.iloc[5] = np.nan
        raw.iloc[4, 1] = np.inf
        result = bounded_policy(raw, times, enabled=True)
        self.assertEqual(result.was_imputed.sum().sum(), 0)
        self.assertTrue(result.values.iloc[[0, 1, 5, 12, 13]].isna().all(axis=None))
        self.assertTrue(result.was_invalid.iloc[4, 1])
        self.assertFalse(result.retained.iloc[4])

    def test_actual_absent_minutes_and_split_crossings_block_whole_runs(self):
        raw, times = fixture()
        raw.iloc[4:6] = np.nan
        broken = times.copy()
        broken.iloc[5:] += pd.Timedelta(minutes=1)
        result = bounded_policy(raw, broken, enabled=True)
        self.assertEqual(result.was_imputed.sum().sum(), 0)
        self.assertEqual(result.runs.iloc[0].reason, "noncontinuous_minutes")
        near_split = pd.Series(pd.date_range("2009-12-31 23:55", periods=len(raw), freq="min"), index=raw.index)
        split_result = bounded_policy(raw, near_split, enabled=True)
        self.assertEqual(split_result.was_imputed.sum().sum(), 0)
        self.assertEqual(split_result.runs.iloc[0].reason, "crosses_model_split")

    def test_partial_fields_preserve_observed_anchors_and_invalid_tokens(self):
        raw, times = fixture()
        raw = raw.astype(object)
        raw.iloc[3, 0] = "?"
        raw.iloc[4, 1] = ""
        raw.iloc[4, 0] = 100.0
        raw.iloc[8, 2] = "junk"
        result = bounded_policy(raw, times, enabled=True)
        self.assertEqual(result.was_imputed.sum().sum(), 2)
        self.assertEqual(result.values.iloc[4, 0], 100.0)
        self.assertEqual(result.values.iloc[3, 0], (raw.iloc[2, 0] + 100) / 2)
        self.assertTrue(result.was_invalid.iloc[8, 2])
        self.assertFalse(result.was_missing.iloc[8, 2])


class ModelingTests(unittest.TestCase):
    def test_strict_threshold_boundary(self):
        np.testing.assert_array_equal(anomaly_flags([-0.7, -0.6, -0.5], -0.6), [True, False, False])

    def test_preprocessing_fitted_on_training_categories_only(self):
        raw, times = fixture(10)
        frame = features(bounded_policy(raw, times))
        frame.loc[frame.index[:6], "Month"] = 1
        frame.loc[frame.index[6:], "Month"] = 12
        pipeline = regression_pipeline("random_forest")
        pipeline.fit(frame.iloc[:6], frame.Global_active_power.iloc[:6])
        categories = pipeline.named_steps["preprocess"].named_transformers_["calendar"].categories_
        self.assertEqual(list(categories[2]), [1])
        self.assertEqual(len(pipeline.predict(frame.iloc[6:])), 4)

    def test_chronological_evaluation_never_uses_imputed_test_targets(self):
        raw, times = fixture(30, "2009-12-31 23:48")
        raw.iloc[3] = np.nan   # Earlier-period imputed training record.
        raw.iloc[20] = np.nan  # Later-period imputed value has no observed target.
        policies = {"observed_only": bounded_policy(raw, times),
                    "short_gap_sensitivity": bounded_policy(raw, times, enabled=True)}
        frames = {name: features(policy) for name, policy in policies.items()}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            regression, _, regression_training = run_regression(policies, frames, output, train_size=8)
            isolation, _, if_training = run_isolation(policies, frames, output, train_size=8, proxy_size=5)
            expected_targets = int(((times >= SPLIT) & policies["observed_only"].retained).sum())
            self.assertTrue(regression.n_observed_targets.eq(expected_targets).all())
            self.assertEqual(isolation.test_imputed_records.tolist(), [0, 1])
            self.assertTrue(isolation.proxy_original_records.eq(5).all())
            for item in regression_training + if_training:
                self.assertLess(times.loc[item["source_row_id"]], SPLIT)
            primary_counts = regression[regression.scenario == "observed_only"].train_records
            sensitivity_counts = regression[regression.scenario == "short_gap_sensitivity"].train_records
            self.assertTrue(primary_counts.eq(8).all())
            self.assertTrue(sensitivity_counts.eq(9).all())
            alerts = pd.read_csv(output / "model_alerts.csv")
            self.assertTrue((alerts.score < alerts.threshold).all())
            self.assertTrue((pd.to_datetime(alerts.timestamp) >= SPLIT).all())
            proxy = pd.read_csv(output / "synthetic_proxy_evaluation.csv")
            self.assertTrue((proxy.alert == (proxy.score < proxy.threshold)).all())
            self.assertTrue(proxy.groupby("scenario").threshold.nunique().eq(1).all())


if __name__ == "__main__":
    unittest.main()
