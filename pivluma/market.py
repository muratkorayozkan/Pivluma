"""Twelve Data daily history and streaming quotes."""

import json
from datetime import date, datetime, timezone
from math import isfinite

from PySide6.QtCore import QObject, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from pivluma.data import Price


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


class MarketFeed(QObject):
    history = Signal(str, object)
    status = Signal(str)
    quote = Signal(float, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.pending = None
        self.socket = None
        self.heartbeat = QTimer(self, interval=10000)
        self.heartbeat.timeout.connect(self.send_heartbeat)

    def stop(self):
        reply, self.pending = self.pending, None
        if reply is not None:
            reply.abort()
        socket, self.socket = self.socket, None
        self.heartbeat.stop()
        if socket is not None:
            socket.abort()
            socket.deleteLater()

    def start(self, symbol, key):
        self.stop()
        url = QUrl("https://api.twelvedata.com/time_series")
        query = QUrlQuery()
        for name, value in (("symbol", symbol), ("interval", "1day"),
                            ("outputsize", "240"), ("format", "JSON")):
            query.addQueryItem(name, value)
        url.setQuery(query)
        request = QNetworkRequest(url)
        request.setRawHeader(b"Authorization", f"apikey {key}".encode("ascii"))
        request.setTransferTimeout(15000)
        reply = self.network.get(request)
        self.pending = reply
        reply.finished.connect(lambda: self.finish_history(reply, symbol, key))
        self.status.emit("Twelve Data · Loading daily history")

    def finish_history(self, reply, symbol, key):
        reply.deleteLater()
        if reply is not self.pending:
            return
        self.pending = None
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.status.emit("Twelve Data · History request failed. Check connection and account access.")
            return
        try:
            prices = history_rows(json.loads(bytes(reply.readAll())))
        except (AttributeError, KeyError, TypeError, ValueError):
            self.status.emit("Twelve Data · Daily history unavailable. Check symbol and account access.")
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
        request = QNetworkRequest(QUrl("wss://ws.twelvedata.com/v1/quotes/price"))
        request.setRawHeader(b"X-TD-APIKEY", key.encode("ascii"))
        self.status.emit("Twelve Data · Connecting quote stream")
        socket.open(request)

    def subscribe(self, socket, symbol):
        if socket is self.socket:
            socket.sendTextMessage(json.dumps({"action": "subscribe", "params": {"symbols": symbol}}))
            self.heartbeat.start()
            self.status.emit("Twelve Data · Awaiting subscription")

    def send_heartbeat(self):
        self.socket.sendTextMessage('{"action":"heartbeat"}')

    def disconnected(self, socket):
        if socket is self.socket:
            self.heartbeat.stop()
            self.status.emit("Twelve Data · Disconnected. Select Connect live to retry.")

    def on_message(self, socket, symbol, message):
        if socket is not self.socket:
            return
        try:
            payload = json.loads(message)
            if payload.get("event") == "subscribe-status":
                accepted = any(row.get("symbol") == symbol for row in payload.get("success", []))
                self.status.emit("Twelve Data · Awaiting first quote" if accepted else
                                 "Twelve Data · Subscription unavailable for this symbol or plan")
            quote = quote_event(payload, symbol)
            if quote is not None:
                self.status.emit('Twelve Data · Quote stream connected')
                self.quote.emit(*quote)
        except (AttributeError, KeyError, OverflowError, OSError, TypeError, ValueError):
            return
