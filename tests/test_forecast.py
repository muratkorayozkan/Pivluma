import unittest

from pivluma.forecast import analyze


class ForecastTests(unittest.TestCase):
    def test_linear_history_and_reference(self):
        result = analyze(list(range(100, 140)), horizon=3)
        self.assertEqual(result.model, "Linear trend")
        self.assertEqual(result.forecast, (140.0, 141.0, 142.0))
        self.assertAlmostEqual(result.mae, 0.0)
        self.assertAlmostEqual(result.baseline_mae, 2.0)

    def test_adaptive_model_avoids_fitting_a_trend_to_constant_prices(self):
        result = analyze([100.0] * 80, horizon=10)
        self.assertEqual(result.model, "Last-price baseline")
        self.assertEqual(result.forecast, (100.0,) * 10)
        self.assertEqual(result.mae, 0.0)
        self.assertIn((10, 32), result.validation)

    def test_final_model_at_long_horizon_uses_matured_errors_only(self):
        result = analyze(list(range(1, 151)), horizon=120)
        self.assertEqual(result.model, "Linear trend")
        self.assertIn((120, 23), result.validation)
        self.assertEqual(result.forecast[-1], 270.0)

    def test_rejects_nonfinite_prices(self):
        with self.assertRaises(ValueError):
            analyze([100.0] * 39 + [float("nan")], horizon=3)
