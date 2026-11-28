"""Daily news-tone observations from GDELT or a user-provided CSV."""

import csv
import json
from datetime import date, datetime, timedelta
from math import isfinite
from time import monotonic

from PySide6.QtCore import QObject, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from pivluma.market import OTHER_SYMBOLS, PUBLIC_ASSETS


GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
QUERIES = {code: f'"{name}"' for code, name in OTHER_SYMBOLS.items() if "/" not in code}
QUERIES.update({code: f'"{name}" cryptocurrency' for code, name in PUBLIC_ASSETS.items()})
QUERIES.update({"BTC": "bitcoin", "ETH": '"Ethereum" cryptocurrency'})


def normalized(value):
    tone = float(value)
    if not isfinite(tone) or not -100 <= tone <= 100:
        raise ValueError("News tone must be between -100 and 100.")
    return max(-1., min(1., tone / 5.))


def timeline_scores(payload):
    timeline = payload["timeline"][0]["data"]
    return {
        datetime.strptime(row["date"][:8], "%Y%m%d").date(): normalized(row["value"])
        for row in timeline if row.get("value") is not None
    }


def read_news_scores(stream):
    reader = csv.DictReader(stream)
    if not reader.fieldnames or not {"date", "tone"} <= set(reader.fieldnames):
        raise ValueError("News CSV requires date and tone columns.")
    result = {}
    for row in reader:
        day = date.fromisoformat(row["date"])
        if day in result:
            raise ValueError("News CSV contains a duplicate date.")
        result[day] = normalized(row["tone"])
    if not result:
        raise ValueError("News CSV contains no scores.")
    return result


def align_scores(prices, scores):
    # A conservative one-calendar-day lag works for both 24h and exchange sessions.
    return [scores.get(price.day - timedelta(days=1)) for price in prices]


class NewsFeed(QObject):
    loaded = Signal(str, object)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self.cache = {}
        self.retry_after = 0

    def cancel(self):
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()

    def start(self, symbol):
        self.cancel()
        query_text = QUERIES.get(symbol) or (
            QUERIES.get(symbol.split("/", 1)[0]) if symbol.endswith(("/USD", "/EUR")) else None
        )
        if not query_text:
            self.status.emit("No automatic news query for this symbol.")
            return
        now = monotonic()
        cached = self.cache.get(query_text)
        if cached and now - cached[0] < 900:
            self.loaded.emit(symbol, cached[1])
            return
        if now < self.retry_after:
            self.status.emit("GDELT is limiting requests. Try again later or import news scores.")
            return
        url = QUrl(GDELT_URL)
        query = QUrlQuery()
        for name, value in (("query", query_text), ("mode", "timelinetone"), ("format", "json"),
                            ("timespan", "90d"), ("timelinesmooth", "0")):
            query.addQueryItem(name, value)
        url.setQuery(query)
        request = QNetworkRequest(url)
        request.setRawHeader(b"User-Agent", b"Pivluma")
        request.setTransferTimeout(12000)
        reply = self.network.get(request)
        self.reply = reply
        reply.finished.connect(lambda: self.finish(reply, symbol, query_text))
        self.status.emit("Loading GDELT news scores…")

    def finish(self, reply, symbol, query):
        reply.deleteLater()
        if reply is not self.reply:
            return
        self.reply = None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status == 429:
            self.retry_after = monotonic() + 120
            self.status.emit("GDELT is limiting requests. Try again later or import news scores.")
            return
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError or reply.bytesAvailable() > 2_000_000:
                raise ValueError()
            scores = timeline_scores(json.loads(bytes(reply.readAll())))
            if not scores:
                raise ValueError()
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            self.status.emit("GDELT news scores are unavailable. Price-only forecasting is still available.")
            return
        self.cache[query] = monotonic(), scores
        self.loaded.emit(symbol, scores)
