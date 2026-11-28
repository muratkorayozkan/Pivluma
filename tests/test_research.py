import unittest
from datetime import date, timedelta

from pivluma.data import Price
from pivluma.research import research_evidence, summarize_research


class ResearchEvidenceTests(unittest.TestCase):
    def test_lagged_association_uses_only_available_price_reactions(self):
        start = date(2025, 1, 1)
        prices = [Price(start, 100)]
        scores = {}
        for step in range(1, 45):
            day = start + timedelta(days=step)
            signal = .8 if step % 2 else -.8
            scores[day - timedelta(days=2)] = signal
            prices.append(Price(day, prices[-1].close * (1 + signal / 100)))
        scores[prices[-1].day] = 1.
        scores[prices[-1].day + timedelta(days=1)] = 1.
        evidence = research_evidence(prices, scores, "GDELT news tone")
        self.assertEqual(evidence["matched_returns"], 44)
        self.assertEqual(evidence["historical_correlation"], 1)
        self.assertEqual(evidence["after_positive_percent"], .8)
        self.assertEqual(evidence["after_negative_percent"], -.8)
        self.assertIn("historical associations", summarize_research(evidence, "en"))

    def test_sparse_or_one_sided_scores_do_not_change_price_direction(self):
        start = date(2025, 1, 1)
        prices = [Price(start + timedelta(days=step), 100 + step) for step in range(45)]
        scores = {price.day: .8 for price in prices[:-1]}
        evidence = research_evidence(prices, scores, "Imported news scores")
        self.assertEqual(evidence["bounded_adjustment_percent"], 0)
        self.assertIsNone(evidence["historical_correlation"])
        self.assertIn("do not support", summarize_research(evidence, "en"))
