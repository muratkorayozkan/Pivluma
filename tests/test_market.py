import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

try:
    from PySide6.QtNetwork import QNetworkReply
    from pivluma.market import MarketFeed, history_rows, quote_event
except ModuleNotFoundError as error:
    if not (error.name or "").startswith("PySide6"):
        raise
    raise unittest.SkipTest("Install requirements-desktop.txt to run market tests.")


class MarketTests(unittest.TestCase):
    def test_history_failure_still_attempts_quote_subscription(self):
        feed = MarketFeed()
        reply = Mock()
        reply.error.return_value = QNetworkReply.NetworkError.ContentAccessDenied
        feed.pending = reply
        statuses = []
        feed.history_warning.connect(statuses.append)
        with patch.object(feed, "open_stream") as connect:
            feed.finish_history(reply, "AAPL", "test-key")
        connect.assert_called_once_with("AAPL", "test-key")
        self.assertIn("History request failed", statuses[-1])

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

    def test_subscription_timeout_closes_socket_and_offers_retry(self):
        feed = MarketFeed()
        socket = Mock()
        feed.socket = socket
        statuses = []
        feed.status.connect(statuses.append)
        feed.subscription_timeout()
        self.assertIsNone(feed.socket)
        socket.abort.assert_called_once()
        self.assertIn("timed out", statuses[-1])
        self.assertIn("retry", statuses[-1])

    def test_rejected_subscription_closes_socket(self):
        feed = MarketFeed()
        socket = Mock()
        feed.socket = socket
        statuses = []
        feed.status.connect(statuses.append)
        feed.on_message(socket, "AAPL", '{"event":"subscribe-status","success":[]}')
        self.assertIsNone(feed.socket)
        socket.abort.assert_called_once()
        self.assertIn("unavailable", statuses[-1])

    def test_accepted_subscription_keeps_socket_open(self):
        feed = MarketFeed()
        socket = Mock()
        feed.socket = socket
        statuses = []
        feed.status.connect(statuses.append)
        feed.on_message(socket, "AAPL", '{"event":"subscribe-status","success":[{"symbol":"AAPL"}]}')
        self.assertIs(feed.socket, socket)
        socket.abort.assert_not_called()
        self.assertIn("Awaiting first quote", statuses[-1])
