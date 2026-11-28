"""Asynchronous GDELT headlines for research and watchlist alerts."""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from pivluma.news import GDELT_URL, QUERIES


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    source: str
    seen: datetime


def parse_articles(payload, now=None):
    now = now or datetime.now(timezone.utc)
    articles = {}
    for row in payload["articles"]:
        try:
            url = row["url"]
            parsed = urlsplit(url)
            title = row["title"].strip()
            seen = datetime.strptime(row["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or not title:
                continue
            if not now - timedelta(days=7) <= seen <= now + timedelta(minutes=5):
                continue
            articles.setdefault(url, Article(title[:300], url, parsed.hostname, seen))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return sorted(articles.values(), key=lambda article: article.seen, reverse=True)


class HeadlineFeed(QObject):
    loaded = Signal(str, object)
    failed = Signal(str)
    retry_at = 0.

    def __init__(self, parent):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.pending = None
        self.cache = {}

    def start(self, symbol):
        if self.pending is not None:
            reply, self.pending = self.pending, None
            reply.abort()
        query_text = QUERIES.get(symbol) or (
            QUERIES.get(symbol.split("/", 1)[0]) if symbol.endswith(("/USD", "/EUR")) else None
        )
        if not query_text:
            self.failed.emit("No automatic news query for this symbol.")
            return
        now = monotonic()
        if symbol in self.cache and now - self.cache[symbol][0] < 600:
            self.loaded.emit(symbol, self.cache[symbol][1])
            return
        if now < HeadlineFeed.retry_at:
            self.failed.emit("Headlines are temporarily unavailable.")
            return
        url = QUrl(GDELT_URL)
        query = QUrlQuery()
        for key, value in (("query", query_text), ("mode", "artlist"), ("format", "json"),
                           ("timespan", "7d"), ("sort", "datedesc"), ("maxrecords", "30")):
            query.addQueryItem(key, value)
        url.setQuery(query)
        request = QNetworkRequest(url)
        request.setTransferTimeout(12000)
        request.setRawHeader(b"User-Agent", b"Pivluma")
        reply = self.pending = self.network.get(request)
        reply.finished.connect(lambda: self.finish(reply, symbol))

    def finish(self, reply, symbol):
        reply.deleteLater()
        if reply is not self.pending:
            return
        self.pending = None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status == 429:
            delay = bytes(reply.rawHeader("Retry-After")).decode("ascii", errors="ignore")
            HeadlineFeed.retry_at = monotonic() + max(120, min(int(delay), 86400) if delay.isdigit() and len(delay) < 10 else 120)
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError or reply.bytesAvailable() > 2_000_000:
                raise ValueError()
            payload = json.loads(bytes(reply.readAll()))
            articles = parse_articles(payload) if payload else []
        except (KeyError, ValueError, TypeError):
            self.failed.emit("Headlines are temporarily unavailable.")
            return
        self.cache[symbol] = monotonic(), articles
        self.loaded.emit(symbol, articles)
