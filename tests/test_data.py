import sqlite3
import unittest
from datetime import date
from io import StringIO

from pivluma.data import PriceStore, read_prices, sample_prices
from pivluma.forecast import analyze


class PriceDataTests(unittest.TestCase):
    def test_csv_sorts_rows_and_accepts_a_bom(self):
        rows = read_prices(StringIO("\ufeffdate,close\n2026-01-02,102\n2026-01-01,101\n"))
        self.assertEqual([(row.day, row.close) for row in rows],
                         [(date(2026, 1, 1), 101.0), (date(2026, 1, 2), 102.0)])

    def test_csv_rejects_invalid_values_and_duplicate_dates(self):
        for contents in ("2026-01-01,nan\n", "2026-01-01,-5\n",
                         "2026-01-01,101\n2026-01-01,102\n", "2026-02-30,10\n"):
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, "Line"):
                read_prices(StringIO("date,close\n" + contents))

    def test_store_preserves_other_series_and_rejects_invalid_replacements(self):
        with sqlite3.connect(":memory:") as connection:
            store = PriceStore(connection)
            first, second = sample_prices()[:2]
            store.replace("DEMO", [first])
            store.replace("OTHER", [second])
            with self.assertRaises(ValueError):
                store.replace("DEMO", [second, second])
            self.assertEqual(store.load("DEMO"), [first])
            store.replace("DEMO", [second])
            self.assertEqual(store.load("DEMO"), [second])
            self.assertEqual(store.load("OTHER"), [second])

    def test_sample_runs_through_forecast(self):
        rows = sample_prices()
        result = analyze([row.close for row in rows], horizon=10)
        self.assertEqual(len(result.forecast), 10)
        self.assertGreater(result.origins, 20)
