import unittest
from datetime import date, timedelta

from pivluma.data import Price
from pivluma.patterns import historical_patterns


class HistoricalPatternTests(unittest.TestCase):
    def test_completed_patterns_include_available_tone_and_subsequent_return(self):
        first = date(2025, 1, 1)
        prices = [Price(first + timedelta(days=step), 100 * (1.01 ** step)) for step in range(100)]
        scores = {price.day: .5 for price in prices[:-1]}
        result = historical_patterns(prices, scores, 5)
        self.assertGreaterEqual(result["matches"], 3)
        self.assertTrue(result["matched_on_research_tone"])
        self.assertEqual(result["median_return_percent"], round((1.01 ** 5 - 1) * 100, 3))
        self.assertEqual(result["positive_outcome_share_percent"], 100)
        self.assertEqual(historical_patterns(prices, scores, 120), {"matches": 0})
