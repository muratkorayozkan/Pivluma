import sqlite3
import unittest
from datetime import date, timedelta

from pivluma.data import Price, sample_prices
from pivluma.lab import ForecastStore, replay, terminal_mae
from pivluma.news import align_scores


class ForecastLabTests(unittest.TestCase):
    def test_replay_has_complete_outcomes_and_ignores_later_news(self):
        prices = sample_prices()
        last = prices[-1].day
        rows = replay(prices, 5, {last + timedelta(days=1): 1}, align_scores)
        self.assertGreater(len(rows), 3)
        for origin, actual, flat, without_news, with_news in rows:
            index = next(i for i, price in enumerate(prices) if price.day == origin)
            self.assertEqual(flat, prices[index].close)
            self.assertEqual(actual, prices[index + 5].close)
            self.assertEqual(without_news, with_news)
        self.assertGreaterEqual(terminal_mae(rows, 2), 0)

    def test_journal_matches_observations_without_counting_missing_calendar_days(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        store = ForecastStore(connection)
        origin = date(2026, 1, 2)
        store.save("DEMO", origin, "llama.cpp", (100, 105))
        store.save("DEMO", origin, "llama.cpp", (100, 105))
        store.reconcile("OTHER", [Price(origin, 101), Price(origin + timedelta(days=3), 102)])
        self.assertEqual(store.runs("DEMO")[0][3], [])
        store.reconcile("DEMO", [Price(origin, 101), Price(origin + timedelta(days=3), 102)])
        partial = store.runs("DEMO")
        self.assertEqual(len(partial), 1)
        self.assertEqual(partial[0][3], [["2026-01-05", 102]])
        store.reconcile("DEMO", [Price(origin + timedelta(days=3), 102),
                                 Price(origin + timedelta(days=4), 108)])
        self.assertEqual(store.runs("DEMO")[0][3][-1], ["2026-01-06", 108])
