import unittest

from pivluma.forecast import analyze


class ForecastTests(unittest.TestCase):
    def test_linear_history_and_reference(self):
        result = analyze(list(range(100, 140)), horizon=3)
        self.assertEqual(result.forecast, (140.0, 141.0, 142.0))
        self.assertAlmostEqual(result.mae, 0.0)
        self.assertAlmostEqual(result.baseline_mae, 2.0)

    def test_rejects_nonfinite_prices(self):
        with self.assertRaises(ValueError):
            analyze([100.0] * 39 + [float("nan")], horizon=3)
