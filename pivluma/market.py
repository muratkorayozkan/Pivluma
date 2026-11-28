"""Daily history and streaming quotes from Kraken and Twelve Data."""

import json
from datetime import date, datetime, timezone
from math import isfinite
from xml.etree import ElementTree

from PySide6.QtCore import QObject, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from pivluma.data import Price


HISTORY_URL = "https://api.twelvedata.com/time_series"
QUOTES_URL = "wss://ws.twelvedata.com/v1/quotes/price"
REST_QUOTE_URL = "https://api.twelvedata.com/quote"
KRAKEN_HISTORY_URL = "https://api.kraken.com/0/public/OHLC"
ECB_HISTORY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
KRAKEN_QUOTES_URL = "wss://ws.kraken.com/v2"

PUBLIC_ASSETS = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP",
    "DOGE": "Dogecoin", "ADA": "Cardano", "AVAX": "Avalanche", "DOT": "Polkadot",
    "LINK": "Chainlink", "LTC": "Litecoin", "BCH": "Bitcoin Cash", "XLM": "Stellar Lumens",
    "UNI": "Uniswap", "ATOM": "Cosmos", "TRX": "TRON", "ETC": "Ethereum Classic",
    "ALGO": "Algorand", "SUI": "Sui", "HBAR": "Hedera", "NEAR": "NEAR Protocol",
    "AAVE": "Aave",
}
PUBLIC_SYMBOLS = {
    f"{base}/{quote}": name
    for base, name in PUBLIC_ASSETS.items()
    for quote in ("USD", "EUR")
}
OTHER_SYMBOLS = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "TSLA": "Tesla",
    "AMZN": "Amazon", "GOOGL": "Alphabet", "META": "Meta Platforms", "AMD": "AMD",
    "NFLX": "Netflix", "JPM": "JPMorgan Chase", "V": "Visa", "WMT": "Walmart",
    "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ", "DIA": "SPDR Dow Jones ETF",
}
FX_SYMBOLS = {
    "EUR/USD": "Euro / US dollar", "USD/TRY": "US dollar / Turkish lira",
    "GBP/USD": "British pound / US dollar", "USD/JPY": "US dollar / Japanese yen",
    "USD/CHF": "US dollar / Swiss franc", "AUD/USD": "Australian dollar / US dollar",
}


def ecb_history_rows(body, symbol):
    base, quote = symbol.split("/")
    days = {}
    for snapshot in ElementTree.fromstring(body).iterfind(".//{*}Cube[@time]"):
        rates = {row.attrib["currency"]: float(row.attrib["rate"]) for row in snapshot}
        rates["EUR"] = 1.0
        if base not in rates or quote not in rates:
            continue
        if not all(isfinite(rates[currency]) and rates[currency] > 0 for currency in (base, quote)):
            continue
        day = date.fromisoformat(snapshot.attrib["time"])
        days[day] = Price(day, rates[quote] / rates[base])
    if not days:
        raise ValueError("ECB returned no daily reference rates.")
    return [days[day] for day in sorted(days)[-240:]]


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
        self.fallback_context = None
        self.rest_timer = QTimer(self, interval=60000)
        self.rest_timer.timeout.connect(self.poll_rest_quote)
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
        self.rest_timer.stop()
        self.fallback_context = None
        self.subscription_deadline.stop()
        if socket is not None:
            socket.abort()
            socket.deleteLater()

    def report(self, message):
        self.status.emit(self.with_provider(message))

    def with_provider(self, message):
        return message.replace("Twelve Data", self.provider, 1)

    def start(self, symbol, key, *, history_only=False):
        self.stop()
        if key is None and symbol in FX_SYMBOLS:
            self.provider = "ECB"
            request = QNetworkRequest(QUrl(ECB_HISTORY_URL))
            request.setTransferTimeout(15000)
            reply = self.network.get(request)
            self.pending = reply
            reply.finished.connect(lambda: self.finish_ecb_history(reply, symbol))
            self.report("Twelve Data · Loading daily history")
            return
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
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                                 QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy)
            request.setRawHeader(b"Authorization", f"apikey {key}".encode("ascii"))
        request.setTransferTimeout(15000)
        reply = self.network.get(request)
        self.pending = reply
        reply.finished.connect(lambda: self.finish_history(reply, symbol, key, history_only=history_only))
        self.report("Twelve Data · Loading daily history")

    def finish_ecb_history(self, reply, symbol):
        reply.deleteLater()
        if reply is not self.pending:
            return
        self.pending = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError("ECB request failed")
            prices = ecb_history_rows(bytes(reply.readAll()), symbol)
        except (ElementTree.ParseError, KeyError, ValueError):
            self.status.emit("ECB · Daily reference rates unavailable.")
            return
        self.history.emit(symbol, prices)
        self.status.emit("ECB · Daily reference rates")

    def finish_history(self, reply, symbol, key, *, history_only=False):
        reply.deleteLater()
        if reply is not self.pending:
            return
        self.pending = None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if key is not None and status == 401:
            self.report("Twelve Data · API key was rejected.")
            self.history_warning.emit("Twelve Data · API key was rejected.")
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            message = "Twelve Data · History request failed. Check connection and account access."
            self.history_warning.emit(self.with_provider(message))
            if not history_only:
                self.open_stream(symbol, key)
            return
        try:
            parse = kraken_history_rows if key is None else history_rows
            prices = parse(json.loads(bytes(reply.readAll())))
        except (AttributeError, IndexError, KeyError, OSError, OverflowError, TypeError, ValueError):
            self.history_warning.emit(
                self.with_provider("Twelve Data · Daily history unavailable. Check symbol and account access.")
            )
            if not history_only:
                self.open_stream(symbol, key)
            return
        self.history.emit(symbol, prices)
        if not history_only:
            self.open_stream(symbol, key)

    def open_stream(self, symbol, key):
        self.fallback_context = (symbol, key) if key is not None else None
        socket = QWebSocket(parent=self)
        self.socket = socket
        socket.connected.connect(lambda: self.subscribe(socket, symbol))
        socket.disconnected.connect(lambda: self.disconnected(socket))
        socket.errorOccurred.connect(lambda error: self.socket_error(socket, error))
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

    def socket_error(self, socket, error):
        if socket is not self.socket:
            return
        if error == QAbstractSocket.SocketError.SslHandshakeFailedError:
            reason = "Secure connection failed."
        elif self.provider == "Twelve Data" and any(
            code in socket.errorString() for code in ("401", "403")
        ):
            reason = "WebSocket access denied for this key or plan."
        else:
            reason = "Could not open the streaming connection."
        self.end_stream(reason)

    def subscription_timeout(self):
        if self.socket is not None:
            self.end_stream("Quote subscription timed out.")

    def end_stream(self, reason):
        fallback = self.fallback_context
        self.stop()
        self.stream_lost.emit()
        if fallback is not None:
            self.fallback_context = fallback
            self.report("Twelve Data · Streaming unavailable. Checking REST quote.")
            self.rest_timer.start()
            self.poll_rest_quote()
        else:
            self.report(f"Twelve Data · {reason} Choose the symbol again to retry.")

    def poll_rest_quote(self):
        if self.fallback_context is None or self.pending is not None:
            return
        symbol, key = self.fallback_context
        url = QUrl(REST_QUOTE_URL)
        query = QUrlQuery()
        query.addQueryItem("symbol", symbol)
        query.addQueryItem("interval", "1min")
        url.setQuery(query)
        request = QNetworkRequest(url)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy)
        request.setRawHeader(b"Authorization", f"apikey {key}".encode("ascii"))
        request.setTransferTimeout(12000)
        reply = self.network.get(request)
        self.pending = reply
        reply.finished.connect(lambda: self.finish_rest_quote(reply, symbol))

    def finish_rest_quote(self, reply, symbol):
        reply.deleteLater()
        if reply is not self.pending:
            return
        self.pending = None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        try:
            payload = json.loads(bytes(reply.readAll()))
            if isinstance(payload, dict) and payload.get("status") == "error":
                status = payload.get("code", status)
            if reply.error() != QNetworkReply.NetworkError.NoError or payload["symbol"] != symbol:
                raise ValueError()
            price = float(payload["close"])
            if not isfinite(price) or price <= 0:
                raise ValueError()
            stamp = datetime.fromtimestamp(int(payload["timestamp"]), timezone.utc)
        except (AttributeError, KeyError, OSError, OverflowError, TypeError, ValueError):
            if str(status) in {"401", "403", "429"}:
                self.rest_timer.stop()
                self.fallback_context = None
                message = "Twelve Data · REST quota reached." if str(status) == "429" else (
                    "Twelve Data · REST quote access denied for this key or plan."
                )
            else:
                message = "Twelve Data · REST quote unavailable. Retrying in one minute."
            self.stream_lost.emit()
            self.report(message)
            return
        self.report("Twelve Data · REST quote · One-minute bar")
        self.quote.emit(price, stamp)

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
