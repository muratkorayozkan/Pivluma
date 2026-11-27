"""Offline market-analysis dashboard."""

import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from pivluma.analyst import answer
from pivluma.data import PriceStore, read_prices, sample_prices
from pivluma.forecast import TRAINING_WINDOW, analyze


from pivluma.market import MarketFeed


class PriceChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(280)
        self.history = []
        self.result = None
        self.setAccessibleName("Closing prices and forecast interval")

    def paintEvent(self, event):
        if not self.history:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#141d2b"))
        chart = QRectF(76, 18, self.width() - 100, self.height() - 52)
        history = self.history[-120:]
        result = self.result
        lower = min(history + list(result.lower))
        upper = max(history + list(result.upper))
        padding = (upper - lower) * .08 or max(upper * .01, .001)
        lower -= padding
        upper += padding
        last = len(history) + len(result.forecast) - 1

        def point(index, price):
            return QPointF(chart.left() + chart.width() * index / last,
                           chart.bottom() - chart.height() * (price - lower) / (upper - lower))

        for line in range(5):
            value = lower + (upper - lower) * line / 4
            left, right = point(0, value), point(last, value)
            painter.setPen(QColor("#2b394d"))
            painter.drawLine(left, right)
            painter.setPen(QColor("#bac8da"))
            painter.drawText(QPointF(12, left.y() + 4), f"{value:.2f}")

        start = len(history) - 1
        top = [point(start, history[-1])] + [
            point(start + step, value) for step, value in enumerate(result.upper, 1)
        ]
        bottom = [
            point(start + step, value) for step, value in enumerate(result.lower, 1)
        ]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#304962"))
        painter.drawPolygon(QPolygonF(top + list(reversed(bottom))))
        painter.setPen(QPen(QColor("#65dfc1"), 2))
        painter.drawPolyline(QPolygonF([point(i, value) for i, value in enumerate(history)]))
        painter.setPen(QPen(QColor("#8bb9ff"), 2, Qt.PenStyle.DashLine))
        painter.drawPolyline(QPolygonF([
            point(start + i, value) for i, value in enumerate([history[-1], *result.forecast])
        ]))
        painter.setPen(QColor("#bac8da"))
        painter.drawText(QPointF(chart.left(), chart.bottom() + 22), "Recent observations")
        painter.end()


class Dashboard(QMainWindow):
    def __init__(self, database: Path):
        super().__init__()
        self.database = database
        self.setWindowTitle("Pivluma")
        self.setMinimumSize(760, 680)
        self.resize(1000, 740)
        page = QWidget()
        self.setCentralWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(14)
        title = QLabel("Pivluma")
        title.setObjectName("title")
        layout.addWidget(title)
        toolbar = QHBoxLayout()
        layout.addLayout(toolbar)
        self.symbol = QLineEdit()
        self.symbol.setPlaceholderText("Symbol")
        self.symbol.setAccessibleName("Symbol")
        toolbar.addWidget(self.symbol)
        for name, action in (("Load saved", self.load_saved), ("Import CSV", self.import_csv),
                             ("Sample data", self.show_sample)):
            button = QPushButton(name)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        toolbar.addStretch()
        toolbar.addWidget(QLabel("Forecast steps"))
        self.horizon = QSpinBox()
        self.horizon.setRange(1, 30)
        self.horizon.setValue(5)
        self.horizon.setAccessibleName("Forecast steps")
        self.horizon.valueChanged.connect(self.refresh)
        toolbar.addWidget(self.horizon)
        self.dataset = QLabel()
        self.dataset.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.dataset)
        self.chart = PriceChart()
        layout.addWidget(self.chart, 1)
        layout.addWidget(QLabel("Closing price  /  Dashed forecast  /  Shaded empirical interval"))
        self.metrics = QLabel()
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        analyst_title = QLabel("Local analyst")
        layout.addWidget(analyst_title)
        analyst_controls = QHBoxLayout()
        self.question = QLineEdit()
        self.question.setAccessibleName("Question for the local analyst")
        self.question.setPlaceholderText("Ask about price movement, forecast, or backtest performance")
        self.question.returnPressed.connect(self.ask_analyst)
        analyst_controls.addWidget(self.question, 1)
        ask = QPushButton("Ask")
        ask.clicked.connect(self.ask_analyst)
        analyst_controls.addWidget(ask)
        layout.addLayout(analyst_controls)
        self.reply = QLabel()
        self.reply.setTextFormat(Qt.TextFormat.PlainText)
        self.reply.setWordWrap(True)
        self.reply.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.reply)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setStyleSheet("color: #ffc181")
        layout.addWidget(self.status)
        market_controls = QHBoxLayout()
        connect = QPushButton("Connect live")
        connect.clicked.connect(self.connect_market)
        disconnect = QPushButton("Disconnect")
        disconnect.clicked.connect(self.stop_market)
        market_controls.addWidget(connect)
        market_controls.addWidget(disconnect)
        self.market_status = QLabel("Twelve Data · Disconnected")
        self.market_status.setWordWrap(True)
        market_controls.addWidget(self.market_status, 1)
        layout.addLayout(market_controls)
        self.quote_label = QLabel()
        self.quote_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.quote_label)
        self.last_quote = None
        self.live_symbol = ""
        self.market = MarketFeed(self)
        self.market.history.connect(self.receive_history)
        self.market.status.connect(self.market_status.setText)
        self.market.quote.connect(self.receive_quote)
        self.quote_timer = QTimer(self, interval=5000)
        self.quote_timer.timeout.connect(self.update_quote)
        self.quote_timer.start()
        self.show_sample()

    def connect_market(self):
        try:
            symbol = self.series_key().upper()
            key, accepted = QInputDialog.getText(self, "Twelve Data", "API key",
                                                   QLineEdit.EchoMode.Password)
            if not accepted:
                return
            key = key.strip()
            if not key or not key.isascii() or not key.isprintable() or " " in key:
                raise ValueError("Enter a valid Twelve Data API key.")
            self.last_quote = None
            self.live_symbol = symbol
            self.market.start(symbol, key)
            self.update_quote()
        except ValueError as error:
            self.status.setText(str(error))

    def stop_market(self):
        self.market.stop()
        self.market_status.setText("Twelve Data · Disconnected")
        self.update_quote()

    def receive_history(self, symbol, prices):
        if len(prices) <= TRAINING_WINDOW + 9:
            self.status.setText("Twelve Data returned too few daily observations for a forecast.")
            return
        self.set_series(f"{symbol} · Twelve Data daily history", prices)

    def receive_quote(self, price, timestamp):
        if self.last_quote is None or timestamp >= self.last_quote[1]:
            self.last_quote = price, timestamp
            self.update_quote()

    def update_quote(self):
        if self.last_quote is None:
            self.quote_label.clear()
            return
        price, timestamp = self.last_quote
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        note = " · Last received quote" if age >= 120 else ""
        self.quote_label.setText(f"{self.live_symbol} · {price:.8g} · "
                                 f"{timestamp:%Y-%m-%d %H:%M:%S UTC}{note}")

    def show_sample(self):
        self.set_series("Synthetic sample", sample_prices())

    def series_key(self):
        symbol = self.symbol.text().strip()
        if not symbol:
            raise ValueError("Enter a symbol first.")
        return symbol

    def load_saved(self):
        try:
            symbol = self.series_key()
            with closing(sqlite3.connect(self.database)) as connection:
                prices = PriceStore(connection).load(symbol)
            if not prices:
                raise ValueError(f"No stored prices for {symbol}.")
            self.set_series(f"{symbol} · Stored CSV", prices)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.status.setText(str(error))

    def import_csv(self):
        try:
            symbol = self.series_key()
            filename, _ = QFileDialog.getOpenFileName(self, "Import closing prices", "",
                                                       "CSV files (*.csv);;All files (*)")
            if not filename:
                return
            with Path(filename).open(encoding="utf-8-sig", newline="") as stream:
                prices = read_prices(stream)
            analyze([price.close for price in prices], 1)
            with closing(sqlite3.connect(self.database)) as connection:
                PriceStore(connection).replace(symbol, prices)
            self.set_series(f"{symbol} · Imported CSV", prices)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.status.setText(str(error))

    def set_series(self, label, prices):
        self.series_label = label
        self.prices = prices
        self.dataset.setText(f"{label}  ·  {prices[0].day} to {prices[-1].day}"
                             f"  ·  {len(prices)} observations")
        self.horizon.blockSignals(True)
        self.horizon.setMaximum(min(30, len(prices) - TRAINING_WINDOW - 9))
        self.horizon.blockSignals(False)
        self.refresh()

    def ask_analyst(self):
        self.reply.setText(answer(self.question.text(), self.series_label, self.prices,
                                  self.chart.result))

    def refresh(self):
        closes = [price.close for price in self.prices]
        result = analyze(closes, self.horizon.value())
        self.chart.history = closes
        self.chart.result = result
        self.chart.update()
        self.metrics.setText(
            f"Last close  {closes[-1]:.2f}     |     Final forecast  {result.forecast[-1]:.2f}"
            f"     |     Model MAE  {result.mae:.2f}     |     Last-price MAE  {result.baseline_mae:.2f}"
            f"     |     Test origins  {result.origins}"
        )
        self.reply.clear()
        self.status.clear()


def main():
    application = QApplication(sys.argv)
    application.setApplicationName("Pivluma")
    application.setStyle("Fusion")
    application.setStyleSheet("""
        QWidget { background: #101722; color: #e7eef8; font-size: 14px; }
        QLabel#title { font-size: 31px; font-weight: 700; }
        QPushButton, QLineEdit, QSpinBox {
            background: #1c293a; border: 1px solid #40526c; border-radius: 6px; padding: 6px 10px;
        }
        QPushButton:hover { background: #2b3c53; }
        QPushButton:focus, QLineEdit:focus, QSpinBox:focus { border-color: #65dfc1; }
    """)
    directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    directory.mkdir(parents=True, exist_ok=True)
    window = Dashboard(directory / "pivluma.sqlite3")
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
