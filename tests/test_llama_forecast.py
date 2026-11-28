import unittest
from datetime import timedelta

from pivluma.data import sample_prices
from pivluma.llama_forecast import anchor_steps, forecast_input, forecast_path


class LocalForecastTests(unittest.TestCase):
    def test_scores_are_recent_lagged_scaled_and_attributed(self):
        prices = sample_prices()
        last = prices[-1].day
        scores = {last - timedelta(days=1): -.25, last: 1, last + timedelta(days=1): 1,
                  last - timedelta(days=31): 1}
        inputs = forecast_input(prices, 20, scores, "GDELT news tone")
        self.assertEqual(inputs["research_source"], "GDELT news tone")
        self.assertEqual(inputs["research_scores"], [{"day": str(last - timedelta(days=1)), "score": -25}])
        self.assertEqual(inputs["requested_steps"], [1, 5, 10, 15, 20])

    def test_path_interpolates_to_model_values_and_rejects_invalid_outputs(self):
        path = forecast_path('{"changes":[1, 5, 10, 15, 21]}', 100, 20)
        self.assertAlmostEqual(path[1], 102)
        self.assertAlmostEqual(path[-1], 121)
        self.assertEqual(len(path), 20)
        for body in ('{"changes":[0]}', '{"changes":[false, 5, 10, 15, 20]}',
                     '{"changes":[NaN, 5, 10, 15, 20]}', '{"changes":[-100, 0, 0, 0, 0]}'):
            with self.assertRaises(ValueError):
                forecast_path(body, 100, 20)
        self.assertEqual(anchor_steps(1), [1])

    def test_model_time_index_echo_rejected_and_news_contribution_bounded(self):
        with self.assertRaises(ValueError):
            forecast_path('{"changes":[1, 2]}', 100, 2)
        body = '{"changes":[0, 0]}'
        rising = forecast_path(body, 100, 2, [{"score": 80}])
        falling = forecast_path(body, 100, 2, [{"score": -80}])
        self.assertAlmostEqual(rising[0], 100.4)
        self.assertAlmostEqual(falling[0], 99.6)
        self.assertLess(rising[1] - falling[1], rising[0] - falling[0])
