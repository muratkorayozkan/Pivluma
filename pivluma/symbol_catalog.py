"""Twelve Data public instrument reference lists for typeahead lookup."""
import json
import re

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


def catalog_codes(body):
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("No symbol catalog")
    return sorted({
        code for item in payload["data"] if isinstance(item, dict)
        and isinstance(code := item.get("symbol"), str)
        and re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^]{0,24}", code)
    })


class SymbolCatalog(QObject):
    loaded = Signal(str, object)

    def __init__(self, parent):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.cache = {}
        self.pending = set()

    def load(self, category):
        if category in self.cache:
            self.loaded.emit(category, self.cache[category])
            return
        if category in self.pending:
            return
        self.pending.add(category)
        resource = {"Stocks": "stocks", "ETFs": "etf"}[category]
        request = QNetworkRequest(QUrl(f"https://api.twelvedata.com/{resource}?country=United%20States"))
        request.setTransferTimeout(10000)
        reply = self.network.get(request)
        reply.finished.connect(lambda: self.finish(category, reply))

    def finish(self, category, reply):
        reply.deleteLater()
        self.pending.discard(category)
        if reply.error() != QNetworkReply.NetworkError.NoError or reply.bytesAvailable() > 12_000_000:
            return
        try:
            codes = catalog_codes(bytes(reply.readAll()))
        except (ValueError, TypeError):
            return
        self.cache[category] = codes
        self.loaded.emit(category, codes)
