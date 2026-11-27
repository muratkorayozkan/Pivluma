"""Exercise the provider client against loopback REST and WebSocket servers."""

import json
import os
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtNetwork import QHostAddress
    from PySide6.QtWebSockets import QWebSocketServer
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as error:
    if not (error.name or "").startswith("PySide6"):
        raise
    raise unittest.SkipTest("Install requirements-desktop.txt to run market integration tests.")

from pivluma import market


class MarketIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_rest_history_then_websocket_quote_and_disconnect(self):
        requests = []
        subscriptions = []
        histories = []
        quotes = []
        statuses = []
        peers = []
        loop = QEventLoop()

        class HistoryHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((urlsplit(self.path), self.headers.get("Authorization")))
                body = json.dumps({"values": [
                    {"datetime": "2026-01-02", "close": "102"},
                    {"datetime": "2026-01-01", "close": "101"},
                ]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        with ExitStack() as cleanup:
            http = cleanup.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), HistoryHandler))
            worker = Thread(target=http.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            worker.start()
            cleanup.callback(worker.join, 2)
            cleanup.callback(http.shutdown)

            server = QWebSocketServer("Pivluma test", QWebSocketServer.SslMode.NonSecureMode)
            self.assertTrue(server.listen(QHostAddress("127.0.0.1"), 0))
            cleanup.callback(server.close)

            def accept_peer():
                peer = server.nextPendingConnection()
                peers.append(peer)

                def respond(message):
                    subscriptions.append(json.loads(message))
                    peer.sendTextMessage('{"event":"subscribe-status","success":[{"symbol":"EUR/USD"}]}')
                    peer.sendTextMessage('{"event":"price","symbol":"EUR/USD","price":1.23,"timestamp":1700000000}')

                peer.textMessageReceived.connect(respond)

            server.newConnection.connect(accept_peer)
            feed = market.MarketFeed()
            cleanup.callback(feed.stop)
            feed.history.connect(lambda symbol, prices: histories.append((symbol, prices)))
            feed.status.connect(statuses.append)

            def receive_quote(price, timestamp):
                quotes.append((price, timestamp))
                peers[-1].close()

            feed.quote.connect(receive_quote)
            feed.stream_lost.connect(loop.quit)
            timeout = QTimer()
            timeout.setSingleShot(True)
            timeout.timeout.connect(loop.quit)
            cleanup.callback(timeout.stop)
            with patch.object(market, "HISTORY_URL", f"http://127.0.0.1:{http.server_port}/time_series"), patch.object(
                market, "QUOTES_URL", f"ws://127.0.0.1:{server.serverPort()}/v1/quotes/price"
            ):
                timeout.start(5000)
                feed.start("EUR/USD", "test-key")
                loop.exec()

            self.assertEqual(len(requests), 1, statuses)
            url, authorization = requests[0]
            self.assertEqual(url.path, "/time_series")
            self.assertEqual(parse_qs(url.query)["symbol"], ["EUR/USD"])
            self.assertEqual(authorization, "apikey test-key")
            self.assertEqual(histories, [("EUR/USD", [
                market.Price(date(2026, 1, 1), 101), market.Price(date(2026, 1, 2), 102)
            ])])
            self.assertEqual(subscriptions, [{"action": "subscribe", "params": {"symbols": "EUR/USD"}}])
            self.assertEqual(quotes, [(1.23, datetime.fromtimestamp(1700000000, timezone.utc))])
            self.assertIn("Disconnected", statuses[-1])
            self.assertIsNone(feed.socket)
