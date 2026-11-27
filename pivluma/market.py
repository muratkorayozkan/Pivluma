"""Daily history and streaming quotes from Kraken and Twelve Data."""

import json
from datetime import date, datetime, timezone
from math import isfinite

from PySide6.QtCore import QObject, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from pivluma.data import Price


HISTORY_URL = "https://api.twelvedata.com/time_series"
QUOTES_URL = "wss://ws.twelvedata.com/v1/quotes/price"
KRAKEN_HISTORY_URL = "https://api.kraken.com/0/public/OHLC"
KRAKEN_QUOTES_URL = "wss://ws.kraken.com/v2"

PUBLIC_SYMBOLS = {
    "BTC/USD": "Bitcoin",
    "ETH/USD": "Ethereum",
    "SOL/USD": "Solana",
    "XRP/USD": "XRP",
    "DOGE/USD": "Dogecoin",
}


def history_rows(payload):
    values = payload.get("values")
    if not isinstance(values, list) or not values:
        raise ValueError("Twelve Data returned no daily prices. Check the symbol and account access.")
    prices = [Price(date.fromisoformat(row["datetime"]), float(row["close"])) for row in values]
    if len({price.day for price in prices}) != len(prices):
        raise ValueError("Twelve Data returned duplicate dates.")
    return sorted(prices, key=lambda price: price.day)


def quote_event(payload, symbol):
    if payload.get("event") != "price" or payload.get("symbol") != symbol:
        return None
    price = float(payload["price"])
    if not isfinite(price) or price <= 0:
        raise ValueError("Invalid quote price.")
    return price, datetime.fromtimestamp(int(payload["timestamp"]), timezone.utc)


def kraken_history_rows(payload):
    if payload.get("error"):
        raise ValueError("Kraken returned an OHLC error.")
    result = payload["result"]
    series = [rows for pair, rows in result.items() if pair != "last"]
    if len(series) != 1 or len(series[0]) < 2:
        raise ValueError("Kraken returned no completed daily candles.")
    return [
        Price(datetime.fromtimestamp(int(row[0]), timezone.utc).date(), float(row[4]))
        for row in series[0][-241:-1]
    ]


def kraken_quote_event(payload, symbol):
    if payload.get("channel") != "ticker":
        return None
    for row in payload["data"]:
        if row.get("symbol") == symbol:
            price = float(row["last"])
            if not isfinite(price) or price <= 0:
                raise ValueError("Invalid quote price.")
            timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("Missing quote time zone.")
            return price, timestamp.astimezone(timezone.utc)
    return None


class MarketFeed(QObject):
    history = Signal(str, object)
    history_warning = Signal(str)
    status = Signal(str)
    quote = Signal(float, object)
    stream_lost = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.pending = None
        self.socket = None
        self.provider = "Twelve Data"
        self.heartbeat = QTimer(self, interval=10000)
        self.heartbeat.timeout.connect(self.send_heartbeat)
        self.subscription_deadline = QTimer(self, interval=15000, singleShot=True)
        self.subscription_deadline.timeout.connect(self.subscription_timeout)

    def stop(self):
        reply, self.pending = self.pending, None
        if reply is not None:
            reply.abort()
        socket, self.socket = self.socket, None
        self.heartbeat.stop()
        self.subscription_deadline.stop()
        if socket is not None:
            socket.abort()
            socket.deleteLater()

    def report(self, message):
        self.status.emit(self.with_provider(message))

    def with_provider(self, message):
        return message.replace("Twelve Data", self.provider, 1)

    def start(self, symbol, key):
        self.stop()
        self.provider = "Kraken" if key is None else "Twelve Data"
        url = QUrl(KRAKEN_HISTORY_URL if key is None else HISTORY_URL)
        query = QUrlQuery()
        parameters = (("pair", symbol.replace("/", "")), ("interval", "1440")) if key is None else (
            ("symbol", symbol), ("interval", "1day"), ("outputsize", "240"), ("format", "JSON")
        )
        for name, value in parameters:
            query.addQueryItem(name, value)
        url.setQuery(query)
        request = QNetworkRequest(url)
        if key is not None:
            request.setRawHeader(b"Authorization", f"apikey {key}".encode("ascii"))
        request.setTransferTimeout(15000)
        reply = self.network.get(request)
        self.pending = reply
        reply.finished.connect(lambda: self.finish_history(reply, symbol, key))
        self.report("Twelve Data · Loading daily history")

    def finish_history(self, reply, symbol, key):
        reply.deleteLater()
        if reply is not self.pending:
            return
        self.pending = None
        if reply.error() != QNetworkReply.NetworkError.NoError:
            message = "Twelve Data · History request failed. Check connection and account access."
            self.history_warning.emit(self.with_provider(message))
            self.open_stream(symbol, key)
            return
        try:
            parse = kraken_history_rows if key is None else history_rows
            prices = parse(json.loads(bytes(reply.readAll())))
        except (AttributeError, IndexError, KeyError, OSError, OverflowError, TypeError, ValueError):
            self.history_warning.emit(
                self.with_provider("Twelve Data · Daily history unavailable. Check symbol and account access.")
            )
            self.open_stream(symbol, key)
            return
        self.history.emit(symbol, prices)
        self.open_stream(symbol, key)

    def open_stream(self, symbol, key):
        socket = QWebSocket(parent=self)
        self.socket = socket
        socket.connected.connect(lambda: self.subscribe(socket, symbol))
        socket.disconnected.connect(lambda: self.disconnected(socket))
        socket.errorOccurred.connect(lambda _: self.disconnected(socket))
        socket.textMessageReceived.connect(lambda message: self.on_message(socket, symbol, message))
        request = QNetworkRequest(QUrl(KRAKEN_QUOTES_URL if key is None else QUOTES_URL))
        if key is not None:
            request.setRawHeader(b"X-TD-APIKEY", key.encode("ascii"))
        self.report("Twelve Data · Connecting quote stream")
        self.subscription_deadline.start()
        socket.open(request)

    def subscribe(self, socket, symbol):
        if socket is self.socket:
            if self.provider == "Kraken":
                message = {"method": "subscribe", "params": {"channel": "ticker", "symbol": [symbol]}}
            else:
                message = {"action": "subscribe", "params": {"symbols": symbol}}
                self.heartbeat.start()
            socket.sendTextMessage(json.dumps(message))
            self.report("Twelve Data · Awaiting subscription")

    def send_heartbeat(self):
        self.socket.sendTextMessage('{"action":"heartbeat"}')

    def disconnected(self, socket):
        if socket is self.socket:
            self.end_stream("Disconnected.")

    def subscription_timeout(self):
        if self.socket is not None:
            self.end_stream("Quote subscription timed out.")

    def end_stream(self, reason):
        self.stop()
        self.stream_lost.emit()
        self.report(f"Twelve Data · {reason} Select Connect live to retry.")

    def on_message(self, socket, symbol, message):
        if socket is not self.socket:
            return
        try:
            payload = json.loads(message)
            kraken = self.provider == "Kraken"
            acknowledgement = (
                payload.get("method") == "subscribe" if kraken
                else payload.get("event") == "subscribe-status"
            )
            if acknowledgement:
                accepted = payload.get("success") is True if kraken else any(
                    row.get("symbol") == symbol for row in payload.get("success", [])
                )
                if not accepted:
                    self.end_stream("Subscription unavailable for this symbol or plan.")
                    return
                self.subscription_deadline.stop()
                self.report("Twelve Data · Awaiting first quote")
            parse = kraken_quote_event if kraken else quote_event
            quote = parse(payload, symbol)
            if quote is not None:
                self.subscription_deadline.stop()
                self.report("Twelve Data · Quote stream connected")
                self.quote.emit(*quote)
        except (AttributeError, KeyError, OverflowError, OSError, TypeError, ValueError):
            return
