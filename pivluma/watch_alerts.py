"""Opt-in market, headline, and forecast alerts for watched symbols."""
import hashlib
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from math import isfinite
from time import monotonic

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSystemTrayIcon,
)

from pivluma.headlines import HeadlineFeed
from pivluma.lab import ForecastStore
from pivluma.market import FX_SYMBOLS, PUBLIC_SYMBOLS, MarketFeed
from pivluma.research_store import ResearchStore, breached_runs


class WatchAlerts(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.config = {}
        self.positions = {}
        self.last_crossing = {}
        self.next_index = 0
        self.market = MarketFeed(self)
        self.market.history.connect(self.receive_history)
        self.headlines = HeadlineFeed(self)
        self.headlines.loaded.connect(self.receive_headlines)
        self.timer = QTimer(self, interval=180_000)
        self.timer.timeout.connect(self.poll)
        self.tray = QSystemTrayIcon(self)
        self.tray.messageClicked.connect(window.activate_existing)

    def load(self):
        if self.window.settings is not None:
            data = self.window.settings.value("watchAlerts", {})
            self.config = data if isinstance(data, dict) else {}
        self.timer.start()

    def poll(self):
        symbols = [symbol for symbol in self.window.watchlist if self.config.get(symbol)]
        if not symbols:
            return
        symbol = symbols[self.next_index % len(symbols)]
        self.next_index += 1
        self.poll_symbol(symbol)

    def poll_symbol(self, symbol):
        config = self.config[symbol]
        if config.get("news"):
            self.headlines.start(symbol)
        if config.get("band") or config.get("threshold"):
            key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
            key = key if key and key.isascii() and key.isprintable() and " " not in key else None
            if symbol in PUBLIC_SYMBOLS:
                self.market.start(symbol, None, history_only=True)
            elif key or symbol in FX_SYMBOLS:
                self.market.start(symbol, key, history_only=True)

    def notify(self, message):
        window = self.window
        window.statusBar().showMessage(message, 15000)
        if QSystemTrayIcon.isSystemTrayAvailable() and QSystemTrayIcon.supportsMessages():
            self.tray.setIcon(window.windowIcon())
            self.tray.show()
            self.tray.showMessage("Pivluma", message, QSystemTrayIcon.MessageIcon.Information)

    def price(self, symbol, value, *, quote=False):
        config = self.config.get(symbol, {})
        if symbol not in self.window.watchlist or not (threshold := config.get("threshold")):
            return
        # A delayed close must not overwrite the state of a fresh live quote.
        active = self.window.last_quote
        if (not quote and symbol == self.window.live_symbol and active and not self.window.stream_lost
                and (datetime.now(timezone.utc) - active[1]).total_seconds() < 120):
            return
        above = value >= threshold
        previous = self.positions.get(symbol)
        self.positions[symbol] = above
        now = monotonic()
        if previous is not None and above != previous and now - self.last_crossing.get(symbol, -60) >= 60:
            self.last_crossing[symbol] = now
            self.notify(self.window.text("Price threshold crossed · {symbol} · {price:.8g} · Level {level:.8g}",
                                         symbol=symbol, price=value, level=threshold))

    def receive_headlines(self, symbol, articles):
        if symbol not in self.window.watchlist or not self.config.get(symbol, {}).get("news"):
            return
        try:
            events = [hashlib.sha256(article.url.encode()).hexdigest() for article in articles]
            with closing(sqlite3.connect(self.window.database)) as connection:
                new, known = ResearchStore(connection).register_events(symbol, "news", events)
            if known and new:
                self.notify(self.window.text("New headlines · {symbol} · {count}", symbol=symbol, count=len(new)))
        except sqlite3.Error:
            return

    def receive_history(self, symbol, prices):
        if not prices or symbol not in self.window.watchlist:
            return
        config = self.config.get(symbol, {})
        today = datetime.now(timezone.utc).date()
        # Some providers include the current, still forming daily bar.
        prices = [price for price in prices if price.day < today]
        if not prices or prices[-1].day < today - timedelta(days=4):
            return
        latest = prices[-1]
        self.price(symbol, latest.close)
        if not config.get("band"):
            return
        try:
            with closing(sqlite3.connect(self.window.database)) as connection:
                ForecastStore(connection).reconcile(symbol, prices)
                new, _ = ResearchStore(connection).register_events(
                    symbol, "band", breached_runs(connection, symbol, latest.day)
                )
            if new:
                self.notify(self.window.text("Closing price outside forecast band · {symbol} · {date} · {count}",
                                             symbol=symbol, date=latest.day, count=len(new)))
        except (sqlite3.Error, ValueError):
            return

    def configure(self):
        window = self.window
        dialog = QDialog(window)
        dialog.setWindowTitle(window.text("Watchlist alerts"))
        layout = QFormLayout(dialog)
        symbol = QComboBox()
        symbol.addItems(window.watchlist)
        symbol.setCurrentText(window.series_symbol)
        threshold = QLineEdit()
        news = QCheckBox()
        band = QCheckBox()
        layout.addRow(window.text("Symbol"), symbol)
        layout.addRow(window.text("Price threshold"), threshold)
        layout.addRow(window.text("New headlines"), news)
        layout.addRow(window.text("Close outside forecast band"), band)
        note = QLabel(window.text("Leave the threshold empty to disable price alerts. Alerts run while Pivluma is open."))
        note.setWordWrap(True)
        layout.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(window.text("Save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(window.text("Cancel"))
        layout.addRow(buttons)

        def show_config():
            config = self.config.get(symbol.currentText(), {})
            threshold.setText(str(config["threshold"]) if config.get("threshold") else "")
            news.setChecked(bool(config.get("news")))
            band.setChecked(bool(config.get("band")))

        def save():
            try:
                level = float(threshold.text().strip().replace(",", ".")) if threshold.text().strip() else None
                if level is not None and (not isfinite(level) or level <= 0):
                    raise ValueError()
            except ValueError:
                note.setText(window.text("Enter a positive price threshold."))
                return
            selected = symbol.currentText()
            if level != self.config.get(selected, {}).get("threshold"):
                self.positions.pop(selected, None)
                self.last_crossing.pop(selected, None)
            if level or news.isChecked() or band.isChecked():
                self.config[selected] = {"threshold": level, "news": news.isChecked(), "band": band.isChecked()}
            else:
                self.config.pop(selected, None)
            if window.settings is not None:
                window.settings.setValue("watchAlerts", self.config)
            dialog.accept()
            if self.config.get(selected):
                self.poll_symbol(selected)

        symbol.currentTextChanged.connect(show_config)
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        show_config()
        dialog.exec()
        dialog.deleteLater()
