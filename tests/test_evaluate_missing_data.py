import unittest

import numpy as np
import pandas as pd

from scripts.evaluate_missing_data import eligible_starts, predictions, sample_nonoverlapping


class MaskingTests(unittest.TestCase):
    def test_known_linear_signal_and_forward_fill_errors(self):
        blocks = np.arange(5, dtype=float)[None, :, None]
        truth, linear, forward = predictions(blocks, 3)
        np.testing.assert_array_equal(linear, truth)
        np.testing.assert_array_equal(forward, np.zeros((1, 3, 1)))
        self.assertEqual(np.abs(forward - truth).mean(), 2)
        self.assertAlmostEqual(np.sqrt(np.square(forward - truth).mean()), np.sqrt(14 / 3))

    def test_predictions_do_not_use_hidden_truth(self):
        first = np.arange(32, dtype=float)[None, :, None]
        altered = first.copy()
        altered[:, 1:31, :] = 999
        _, linear, forward = predictions(first, 30)
        _, changed_linear, changed_forward = predictions(altered, 30)
        np.testing.assert_array_equal(linear, changed_linear)
        np.testing.assert_array_equal(forward, changed_forward)

    def test_missing_invalid_and_broken_timestamps_break_eligibility(self):
        times = pd.Series(pd.date_range("2020-01-01", periods=100, freq="min"))
        values = np.ones((100, 7))
        values[35, 0] = np.nan
        values[60, 1] = -1
        times.iloc[80] += pd.Timedelta(seconds=30)
        starts = eligible_starts(values, times, window=5)
        for start in starts:
            self.assertTrue(np.isfinite(values[start:start + 5]).all())
            self.assertTrue((values[start:start + 5] >= 0).all())
            self.assertTrue((times.iloc[start:start + 5].diff().dropna() == pd.Timedelta(minutes=1)).all())
        self.assertNotIn(32, starts)
        self.assertNotIn(57, starts)
        self.assertNotIn(78, starts)
        self.assertIn(0, starts)

    def test_absent_and_duplicate_minutes_are_not_crossed(self):
        times = pd.Series(pd.date_range("2020-01-01", periods=10, freq="min"))
        times.iloc[4] = times.iloc[3]
        starts = eligible_starts(np.ones((10, 7)), times, window=3)
        self.assertNotIn(2, starts)
        self.assertNotIn(3, starts)
        self.assertNotIn(4, starts)

    def test_reproducible_nonoverlapping_sampling(self):
        first = sample_nonoverlapping(np.arange(1000), 10, np.random.default_rng(42))
        second = sample_nonoverlapping(np.arange(1000), 10, np.random.default_rng(42))
        np.testing.assert_array_equal(first, second)
        self.assertTrue((np.diff(first) >= 32).all())
        with self.assertRaises(ValueError):
            sample_nonoverlapping(np.arange(10), 2, np.random.default_rng(42))


if __name__ == "__main__":
    unittest.main()
