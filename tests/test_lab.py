import sqlite3
import unittest
from datetime import date, timedelta

from pivluma.data import Price, sample_prices
from pivluma.lab import ForecastStore, paired_input_errors, replay, terminal_mae
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

    def test_journal_does_not_fill_steps_across_disconnected_history(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        store = ForecastStore(connection)
        origin = date(2026, 1, 2)
        store.save("S", origin, "llama.cpp", (101, 102))
        store.reconcile("S", [Price(origin, 100), Price(origin + timedelta(days=1), 101)])
        store.reconcile("S", [Price(origin + timedelta(days=20), 120), Price(origin + timedelta(days=21), 121)])
        self.assertEqual(store.runs("S")[0][3], [["2026-01-03", 101]])

    def test_calibration_requires_prospective_records_and_reports_rolling_coverage(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        store = ForecastStore(connection)
        initial = date(2026, 1, 1)
        for index in range(25):
            origin = initial + timedelta(days=index)
            store.save("S", origin, "llama.cpp", (100,), origin_close=100,
                       recorded_on=origin, bands=[(98, 101)])
        recent = initial + timedelta(days=25)
        store.save("S", recent, "llama.cpp", (1,), origin_close=100, recorded_on=recent + timedelta(days=2))
        history = [Price(initial + timedelta(days=index), 102) for index in range(27)]
        store.reconcile("S", history)
        first, second = store.calibrate("S", recent + timedelta(days=1), 200, (200, 200))
        self.assertEqual(first.samples, 25)
        self.assertEqual((first.lower, first.upper), (196, 204))
        self.assertEqual((first.covered, first.checked), (5, 5))
        self.assertIsNone(second)
        self.assertGreater(next(store.watch_rows(["S"]))[4], 0)

    def test_paired_errors_drop_incomplete_origins_for_every_variant(self):
        variants = ("price", "news")
        data = {1: (14., 10., {"price": 13., "news": 14.}), 2: (20., 10., {"news": 10.})}
        count, flat, errors = paired_input_errors(data, variants)
        self.assertEqual((count, flat, errors), (1, 4., {"price": 1., "news": 0.}))
