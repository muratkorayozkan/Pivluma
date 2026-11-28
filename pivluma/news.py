"""Daily news-tone observations from GDELT or a user-provided CSV."""

import csv
import json
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from time import monotonic, time

from PySide6.QtCore import QObject, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from pivluma.market import OTHER_SYMBOLS, PUBLIC_ASSETS


SENTIMENT_URL = "https://api.alternative.me/fng/?limit=90"
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


def sentiment_scores(payload):
    return {
        datetime.fromtimestamp(int(row["timestamp"]), timezone.utc).date(): (float(row["value"]) - 50) / 50
        for row in payload["data"] if 0 <= float(row["value"]) <= 100
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
    loaded = Signal(str, object, str)
    status = Signal(str)

    def __init__(self, parent=None, cache_path=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self.cache_path = cache_path
        self.cache = self.read_cache()
        self.retry_after = 0

    def read_cache(self):
        if self.cache_path is None:
            return {}
        try:
            if self.cache_path.stat().st_size > 1_000_000:
                return {}
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return {key: (float(value[0]), {date.fromisoformat(day): float(score)
                                              for day, score in value[1].items()
                                              if -1 <= float(score) <= 1})
                    for key, value in raw.items()}
        except (AttributeError, IndexError, OSError, TypeError, ValueError):
            return {}

    def save_cache(self):
        if self.cache_path is None:
            return
        try:
            payload = {key: (stamp, {str(day): score for day, score in values.items()})
                       for key, (stamp, values) in self.cache.items()
                       if time() - stamp < 7 * 86400}
            temporary = self.cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(self.cache_path)
        except OSError:
            pass

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
        if cached and time() - cached[0] < 900:
            self.loaded.emit(symbol, cached[1], "GDELT news tone (cached)")
            return
        if now < self.retry_after:
            self.use_fallback(symbol, query_text, "GDELT is limiting requests. Try again later or import news scores.")
            return
        url = QUrl(GDELT_URL)
        query = QUrlQuery()
        for name, value in (("query", query_text), ("mode", "timelinetone"), ("format", "json"),
                            ("timespan", "3months"), ("timelinesmooth", "1")):
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
            retry = bytes(reply.rawHeader(b"Retry-After")).decode("ascii", errors="ignore")
            self.retry_after = monotonic() + max(120, min(int(retry), 86400) if retry.isdigit() else 120)
            self.use_fallback(symbol, query, "GDELT is limiting requests. Try again later or import news scores.")
            return
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError or reply.bytesAvailable() > 2_000_000:
                raise ValueError()
            scores = timeline_scores(json.loads(bytes(reply.readAll())))
            if not scores:
                raise ValueError()
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            self.use_fallback(symbol, query, "GDELT news scores are unavailable. Price-only forecasting is still available.")
            return
        self.cache[query] = time(), scores
        self.save_cache()
        self.loaded.emit(symbol, scores, "GDELT news tone")

    def use_fallback(self, symbol, query, failure):
        cached = self.cache.get(query)
        if cached and time() - cached[0] < 7 * 86400:
            self.loaded.emit(symbol, cached[1], "GDELT news tone (cached)")
            return
        if symbol.split("/", 1)[0] != "BTC":
            self.status.emit(failure)
            return
        cached = self.cache.get("alternative-me-btc")
        if cached and time() - cached[0] < 3600:
            self.loaded.emit(symbol, cached[1], "Alternative.me BTC sentiment")
            return
        request = QNetworkRequest(QUrl(SENTIMENT_URL))
        request.setTransferTimeout(10000)
        reply = self.network.get(request)
        self.reply = reply
        reply.finished.connect(lambda: self.finish_sentiment(reply, symbol, failure))
        self.status.emit("Loading BTC sentiment from Alternative.me…")

    def finish_sentiment(self, reply, symbol, failure):
        reply.deleteLater()
        if reply is not self.reply:
            return
        self.reply = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError or reply.bytesAvailable() > 250000:
                raise ValueError()
            scores = sentiment_scores(json.loads(bytes(reply.readAll())))
            if not scores:
                raise ValueError()
        except (AttributeError, IndexError, KeyError, OSError, OverflowError, TypeError, ValueError):
            self.status.emit(failure)
            return
        self.cache["alternative-me-btc"] = time(), scores
        self.save_cache()
        self.loaded.emit(symbol, scores, "Alternative.me BTC sentiment")
