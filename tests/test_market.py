import unittest
from datetime import datetime, timezone

try:
    from pivluma.market import history_rows, quote_event
except ModuleNotFoundError as error:
    if not (error.name or "").startswith("PySide6"):
        raise
    raise unittest.SkipTest("Install requirements-desktop.txt to run market tests.")


class MarketTests(unittest.TestCase):
    def test_history_is_sorted(self):
        rows = history_rows({"values": [
            {"datetime": "2026-01-02", "close": "102"},
            {"datetime": "2026-01-01", "close": "101"},
        ]})
        self.assertEqual([row.close for row in rows], [101, 102])

    def test_provider_error_is_rejected(self):
        with self.assertRaises(ValueError):
            history_rows({"status": "error"})

    def test_quote_uses_provider_timestamp_and_symbol(self):
        message = {"event": "price", "symbol": "BTC/USD", "price": 123, "timestamp": 0}
        self.assertIsNone(quote_event(message, "AAPL"))
        self.assertEqual(quote_event(message, "BTC/USD"),
                         (123, datetime(1970, 1, 1, tzinfo=timezone.utc)))
