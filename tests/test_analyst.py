import unittest
from datetime import date, timedelta

from pivluma.analyst import answer, price_statistics
from pivluma.data import Price, sample_prices
from pivluma.forecast import analyze


class AnalystTests(unittest.TestCase):
    def setUp(self):
        self.prices = sample_prices()
        self.result = analyze([price.close for price in self.prices], 5)

    def ask(self, question):
        return answer(question, "Synthetic sample", self.prices, self.result)

    def test_moving_average_uses_latest_twenty_closes(self):
        expected = sum(price.close for price in self.prices[-20:]) / 20
        self.assertIn(f"{expected:.2f}", self.ask("What is the moving average?"))
        reply = answer("Hareketli ortalama nedir?", "DEMO", self.prices, self.result, "tr")
        self.assertIn("20 gözlem", reply)
        self.assertIn(f"{expected:.2f}", reply)

    def test_forecast_reports_horizon_and_range(self):
        reply = self.ask("What is the forecast?")
        self.assertIn("5 observations ahead", reply)
        self.assertIn(f"{self.result.forecast[-1]:.2f}", reply)
        self.assertIn(f"{self.result.lower[-1]:.2f}", reply)

    def test_backtest_compares_against_baseline(self):
        reply = self.ask("How accurate was the model?")
        self.assertIn(f"{self.result.baseline_mae:.2f}", reply)
        self.assertIn(f"{self.result.mae:.2f}", reply)
        self.assertIn("The difference was", reply)
        self.assertIn(str(self.result.origins), reply)

    def test_price_change_includes_source_and_period(self):
        reply = self.ask("How did the price move?")
        self.assertIn("Synthetic sample", reply)
        self.assertIn(str(self.prices[0].day), reply)
        self.assertIn(str(self.prices[-1].day), reply)

    def test_built_in_statistics_are_localized_and_based_on_closes(self):
        start = date(2025, 1, 1)
        closes = [80., 100., 75., 90.] + [90.] * 11
        prices = [Price(start + timedelta(days=day), value) for day, value in enumerate(closes)]
        stats = price_statistics(prices)
        self.assertAlmostEqual(stats["max_drawdown_percent"], 25)
        self.assertAlmostEqual(stats["rsi_14_simple"], 100 * 35 / 60)
        self.assertIn("25.00%", answer("What was the maximum drawdown?", "DEMO", prices, self.result))
        self.assertIn("最高收盘价为 100", answer("最高收盘价", "DEMO", prices, self.result, "zh"))
        self.assertIn("58.3", answer("RSI nedir?", "DEMO", prices, self.result, "tr"))
        self.assertIn("oynaklık", answer("Oynaklık nedir?", "DEMO", prices, self.result, "tr"))

    def test_unsupported_questions_get_supported_topics(self):
        self.assertIn("supported topics", self.ask("Who runs this company?"))
