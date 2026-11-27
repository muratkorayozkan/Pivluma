"""Offline market-analysis dashboard."""

import csv
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QPointF, QProcess, QRectF, QSettings, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetricsF, QIcon, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from pivluma import __version__
from pivluma.analyst import answer
from pivluma.data import PriceStore, read_prices, sample_prices
from pivluma.forecast import TRAINING_WINDOW, analyze
from pivluma.localization import translate
from pivluma.market import PUBLIC_SYMBOLS, MarketFeed
from pivluma.update_ui import UpdateController


class PriceChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(280)
        self.history = []
        self.result = None
        self.language = "en"
        self.setAccessibleName("Closing prices and forecast interval")

    def paintEvent(self, event):
        if not self.history:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#141d2b"))
        history = self.history[-120:]
        result = self.result
        lower = min(history + list(result.lower))
        upper = max(history + list(result.upper))
        padding = (upper - lower) * .08 or max(upper * .01, .001)
        lower -= padding
        upper += padding
        values = [lower + (upper - lower) * index / 4 for index in range(5)]
        labels = [f"{value:.2f}" if abs(value) < 1_000_000 else f"{value:.3g}" for value in values]
        font = QFontMetricsF(painter.font())
        left_margin = max(font.horizontalAdvance(label) for label in labels) + 24
        top_margin = font.height() + 8
        bottom_margin = font.height() * 2 + 12
        chart = QRectF(left_margin, top_margin, self.width() - left_margin - 24,
                       self.height() - top_margin - bottom_margin)
        last = len(history) + len(result.forecast) - 1

        def point(index, price):
            return QPointF(chart.left() + chart.width() * index / last,
                           chart.bottom() - chart.height() * (price - lower) / (upper - lower))

        for value, label in zip(values, labels):
            left, right = point(0, value), point(last, value)
            painter.setPen(QColor("#2b394d"))
            painter.drawLine(left, right)
            painter.setPen(QColor("#bac8da"))
            label_x = chart.left() - font.horizontalAdvance(label) - 12
            painter.drawText(QPointF(label_x, left.y() + font.ascent() / 2), label)

        painter.save()
        painter.setClipRect(chart.adjusted(-1, -1, 1, 1))
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
        painter.restore()
        painter.setPen(QColor("#bac8da"))
        painter.drawText(
            QPointF(chart.left(), chart.bottom() + font.height() + 6),
            translate(self.language, "Recent observations"),
        )
        painter.end()


class Dashboard(QMainWindow):
    def __init__(self, database: Path):
        super().__init__()
        self.database = database
        self.language = "en"
        self.localized = []
        self.market_message = "Twelve Data · Disconnected"
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("pivluma.ico"))))
        self.setWindowTitle(f"Pivluma {__version__}")
        self.setMinimumSize(760, 680)
        self.resize(1100, 850)
        page = QWidget()
        self.scroll_area = QScrollArea()
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(page)
        self.setCentralWidget(self.scroll_area)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(14)
        title = QLabel("Pivluma")
        title.setObjectName("title")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.localized_label("Language"))
        self.language_picker = QComboBox()
        self.language_picker.addItem("EN · English", "en")
        self.language_picker.addItem("TR · Türkçe", "tr")
        self.language_picker.currentIndexChanged.connect(self.change_language)
        header.addWidget(self.language_picker)
        self.update_button = self.localized_button("Check for updates")
        self.update_button.hide()
        header.addWidget(self.update_button)
        layout.addLayout(header)
        self.update_message = ""
        self.update_label = QLabel()
        self.update_label.setWordWrap(True)
        self.update_label.hide()
        layout.addWidget(self.update_label)
        market_section = QVBoxLayout()
        market_heading = self.localized_label("Market data")
        market_heading.setProperty("sectionTitle", True)
        market_section.addWidget(market_heading)
        layout.addLayout(market_section)
        toolbar = QHBoxLayout()
        market_section.addLayout(toolbar)
        self.symbol = QLineEdit()
        self.localize(self.symbol.setPlaceholderText, "Symbol")
        self.localize(self.symbol.setAccessibleName, "Symbol")
        toolbar.addWidget(self.symbol)
        for name, action in (("Load saved", self.load_saved), ("Import CSV", self.import_csv),
                             ("Sample data", self.show_sample)):
            button = self.localized_button(name)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        toolbar.addStretch()
        forecast_controls = QHBoxLayout()
        layout.addLayout(forecast_controls)
        forecast_controls.addWidget(self.localized_label("Forecast steps"))
        self.horizon = QSpinBox()
        self.horizon.setRange(1, 30)
        self.horizon.setValue(5)
        self.localize(self.horizon.setAccessibleName, "Forecast steps")
        self.horizon.valueChanged.connect(self.refresh)
        forecast_controls.addWidget(self.horizon)
        forecast_controls.addStretch()
        self.dataset = QLabel()
        self.dataset.setTextFormat(Qt.TextFormat.PlainText)
        self.dataset.setWordWrap(True)
        self.dataset.setObjectName("dataset")
        layout.addWidget(self.dataset)
        self.chart = PriceChart()
        layout.addWidget(self.chart, 1)
        self.chart_legend = self.localized_label(
            "Closing price  /  Dashed forecast  /  Shaded empirical interval"
        )
        self.chart_legend.setWordWrap(True)
        layout.addWidget(self.chart_legend)
        self.metrics = QLabel()
        self.metrics.setWordWrap(True)
        self.metrics.setObjectName("metrics")
        layout.addWidget(self.metrics)
        export = self.localized_button("Export forecast CSV")
        export.clicked.connect(self.export_forecast)
        forecast_controls.addWidget(export)
        analyst_title = self.localized_label("Local analyst")
        analyst_title.setProperty("sectionTitle", True)
        layout.addWidget(analyst_title)
        prompts = QHBoxLayout()
        for label, question in (
            ("Price movement", "How did the price move?"),
            ("Forecast", "What is the forecast?"),
            ("Moving average", "What is the moving average?"),
            ("Backtest", "How accurate was the model?"),
        ):
            topic = self.localized_button(label)
            topic.clicked.connect(lambda checked=False, prompt=question: self.ask_topic(prompt))
            prompts.addWidget(topic)
        prompts.addStretch()
        layout.addLayout(prompts)
        analyst_controls = QHBoxLayout()
        self.question = QLineEdit()
        self.localize(self.question.setAccessibleName, "Question for the local analyst")
        self.localize(self.question.setPlaceholderText,
                      "Ask about price movement, forecast, or backtest performance")
        self.question.returnPressed.connect(self.ask_analyst)
        analyst_controls.addWidget(self.question, 1)
        ask = self.localized_button("Ask")
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
        connect = self.localized_button("Connect live")
        connect.clicked.connect(self.connect_market)
        disconnect = self.localized_button("Disconnect")
        disconnect.clicked.connect(self.stop_market)
        choose = self.localized_button("Choose symbol")
        choose.clicked.connect(self.choose_symbol)
        market_controls.addWidget(choose)
        market_controls.addWidget(connect)
        market_controls.addWidget(disconnect)
        self.market_status = QLabel("Twelve Data · Disconnected")
        self.market_status.setWordWrap(True)
        market_controls.addWidget(self.market_status, 1)
        market_section.addLayout(market_controls)
        self.quote_label = QLabel()
        self.quote_label.setTextFormat(Qt.TextFormat.PlainText)
        self.quote_label.setWordWrap(True)
        self.quote_label.setObjectName("quote")
        market_section.addWidget(self.quote_label)
        self.history_warning = QLabel()
        self.history_warning.setWordWrap(True)
        self.history_warning.hide()
        market_section.addWidget(self.history_warning)
        self.history_message = ""
        self.last_quote = None
        self.stream_lost = True
        self.live_symbol = ""
        self.market = MarketFeed(self)
        self.market.history.connect(self.receive_history)
        self.market.history_warning.connect(self.set_history_warning)
        self.market.status.connect(self.set_market_status)
        self.market.quote.connect(self.receive_quote)
        self.market.stream_lost.connect(self.mark_stream_lost)
        self.quote_timer = QTimer(self, interval=5000)
        self.quote_timer.timeout.connect(self.update_quote)
        self.quote_timer.start()
        self.show_sample()

    def text(self, message, **values):
        return translate(self.language, message).format(**values)

    def localize(self, setter, message):
        self.localized.append((setter, message))
        setter(self.text(message))

    def localized_label(self, message):
        label = QLabel()
        self.localize(label.setText, message)
        return label

    def localized_button(self, message):
        button = QPushButton()
        self.localize(button.setText, message)
        return button

    def change_language(self):
        self.language = self.language_picker.currentData()
        for setter, message in self.localized:
            setter(self.text(message))
        self.language_picker.setAccessibleName(self.text("Language"))
        self.chart.language = self.language
        self.chart.setAccessibleName(self.text("Closing prices and forecast interval"))
        self.chart.update()
        self.set_market_status(self.market_message)
        self.set_history_warning(self.history_message)
        self.update_quote()
        self.update_summary()
        self.update_label.setText(self.text(self.update_message))
        if self.reply.text():
            self.ask_analyst()
        self.status.clear()

    def set_market_status(self, message):
        self.market_message = message
        self.market_status.setText(self.text(message))

    def set_history_warning(self, message):
        self.history_message = message
        self.history_warning.setText(self.text(message))
        self.history_warning.setVisible(bool(message))

    def choose_symbol(self):
        instruments = {
            **PUBLIC_SYMBOLS,
            "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "TSLA": "Tesla",
            "SPY": "SPDR S&P 500 ETF", "EUR/USD": "Euro / US dollar",
            "USD/TRY": "US dollar / Turkish lira",
        }
        choices = {f"{self.text(name)} ({symbol})": symbol for symbol, name in instruments.items()}
        current = self.symbol.text().strip().upper()
        index = next((i for i, symbol in enumerate(choices.values()) if symbol == current), 0)
        choice, accepted = QInputDialog.getItem(
            self, self.text("Choose symbol"), self.text("Market instrument"), list(choices), index, False
        )
        if accepted:
            self.symbol.setText(choices[choice])
            return choices[choice]
        return None

    def connect_market(self):
        try:
            symbol = self.symbol.text().strip().upper() or self.choose_symbol()
            if symbol is None:
                return
            if ',' in symbol:
                raise ValueError(self.text("Enter one symbol for live data, such as AAPL or EUR/USD."))
            key = None
            if symbol not in PUBLIC_SYMBOLS:
                key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
                if not key or not key.isascii() or not key.isprintable() or " " in key:
                    raise ValueError(self.text(
                        "Live access is not configured for this symbol. Choose a public crypto symbol."
                    ))
            self.last_quote = None
            self.live_symbol = symbol
            self.set_history_warning("")
            self.market.start(symbol, key)
            self.update_quote()
        except ValueError as error:
            self.status.setText(str(error))

    def stop_market(self):
        self.market.stop()
        self.set_market_status(self.market.with_provider("Twelve Data · Disconnected"))
        self.mark_stream_lost()

    def receive_history(self, symbol, prices):
        if len(prices) <= TRAINING_WINDOW + 9:
            self.status.setText(self.text("Twelve Data returned too few daily observations for a forecast."))
            return
        self.set_series(self.market.with_provider("Twelve Data daily history"), prices, symbol)

    def receive_quote(self, price, timestamp):
        if self.last_quote is None or timestamp >= self.last_quote[1]:
            self.stream_lost = False
            self.last_quote = price, timestamp
            self.update_quote()

    def update_quote(self):
        if self.last_quote is None:
            self.quote_label.clear()
            return
        price, timestamp = self.last_quote
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        note = f" · {self.text('Last received quote')}" if self.stream_lost or age >= 120 else ""
        self.quote_label.setText(f"{self.live_symbol} · {price:.8g} · "
                                 f"{timestamp:%Y-%m-%d %H:%M:%S UTC}{note}")

    def mark_stream_lost(self):
        self.stream_lost = True
        self.update_quote()

    def show_offline_series(self, label, prices, symbol=""):
        self.last_quote = None
        self.live_symbol = ""
        self.stop_market()
        self.set_history_warning("")
        self.set_series(label, prices, symbol)

    def show_sample(self):
        self.show_offline_series("Synthetic sample", sample_prices())

    def series_key(self):
        symbol = self.symbol.text().strip()
        if not symbol:
            raise ValueError(self.text("Enter a symbol first."))
        return symbol

    def load_saved(self):
        try:
            with closing(sqlite3.connect(self.database)) as connection:
                store = PriceStore(connection)
                symbols = store.symbols()
                if not symbols:
                    self.status.setText(self.text("Import a CSV before opening a saved series."))
                    return
                current = self.symbol.text().strip()
                index = symbols.index(current) if current in symbols else 0
                symbol, accepted = QInputDialog.getItem(
                    self, self.text("Load saved"), self.text("Saved symbol"), symbols, index, False
                )
                if not accepted:
                    return
                prices = store.load(symbol)
            if len(prices) <= TRAINING_WINDOW + 9:
                raise ValueError(self.text("Not enough stored prices for {symbol}.", symbol=symbol))
            self.symbol.setText(symbol)
            self.show_offline_series("Stored CSV", prices, symbol)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.status.setText(str(error))

    def import_csv(self):
        try:
            symbol = self.series_key()
            filename, _ = QFileDialog.getOpenFileName(self, self.text("Import closing prices"), "",
                                                       self.text("CSV files (*.csv);;All files (*)"))
            if not filename:
                return
            with Path(filename).open(encoding="utf-8-sig", newline="") as stream:
                prices = read_prices(stream)
            analyze([price.close for price in prices], 1)
            with closing(sqlite3.connect(self.database)) as connection:
                PriceStore(connection).replace(symbol, prices)
            self.show_offline_series("Imported CSV", prices, symbol)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.status.setText(str(error))

    def set_series(self, label, prices, symbol=""):
        self.series_source = label
        self.series_symbol = symbol
        self.prices = prices
        self.horizon.blockSignals(True)
        self.horizon.setMaximum(min(30, len(prices) - TRAINING_WINDOW - 9))
        self.horizon.blockSignals(False)
        self.refresh()

    def ask_topic(self, prompt):
        self.question.setText(self.text(prompt))
        self.ask_analyst()

    def ask_analyst(self):
        self.reply.setText(answer(self.question.text(), self.series_label, self.prices,
                                  self.chart.result, self.language))

    def export_forecast(self):
        filename, _ = QFileDialog.getSaveFileName(self, self.text("Export forecast"), "forecast.csv",
                                                   self.text("CSV files (*.csv)"))
        if not filename:
            return
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(".csv")
        result = self.chart.result
        try:
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("last_observed_date", "step", "forecast", "lower", "upper"))
                for step, (forecast, lower, upper) in enumerate(
                    zip(result.forecast, result.lower, result.upper), 1
                ):
                    writer.writerow((self.prices[-1].day, step, forecast, lower, upper))
        except OSError as error:
            self.status.setText(self.text("Could not export forecast: {error}", error=error))
            return
        self.status.setText(self.text("Forecast exported to {path}", path=path))

    def refresh(self):
        closes = [price.close for price in self.prices]
        result = analyze(closes, self.horizon.value())
        self.chart.history = closes
        self.chart.result = result
        self.chart.update()
        self.update_summary()
        self.reply.clear()
        self.status.clear()

    def update_summary(self):
        source = self.text(self.series_source)
        self.series_label = f"{self.series_symbol} · {source}" if self.series_symbol else source
        self.dataset.setText(self.text(
            "{label}  ·  {start} to {end}  ·  {count} observations",
            label=self.series_label, start=self.prices[0].day, end=self.prices[-1].day,
            count=len(self.prices),
        ))
        result = self.chart.result
        fields = (
            ("Last close", f"{self.prices[-1].close:.2f}"),
            ("Final forecast", f"{result.forecast[-1]:.2f}"),
            ("Model MAE", f"{result.mae:.2f}"),
            ("Last-price MAE", f"{result.baseline_mae:.2f}"),
            ("Test origins", str(result.origins)),
        )
        self.metrics.setText("     |     ".join(f"{self.text(label)}  {value}" for label, value in fields))


def main():
    application = QApplication(sys.argv)
    application.setApplicationName("Pivluma")
    application.setApplicationVersion(__version__)
    application.setStyle("Fusion")
    application.setStyleSheet("""
        QWidget { background: #101722; color: #e7eef8; font-size: 14px; }
        QLabel#title { font-size: 31px; font-weight: 700; }
        QLabel[sectionTitle="true"] { font-size: 17px; font-weight: 650; color: #d9e9fb; }
        QLabel#dataset { font-size: 16px; font-weight: 650; }
        QLabel#metrics { background: #182436; border: 1px solid #30455e; border-radius: 8px; padding: 10px; }
        QLabel#quote { color: #72e5c8; font-weight: 650; }
        QPushButton, QLineEdit, QSpinBox {
            background: #1c293a; border: 1px solid #40526c; border-radius: 6px; padding: 6px 10px;
        }
        QPushButton:hover { background: #2b3c53; }
        QPushButton:focus, QLineEdit:focus, QSpinBox:focus { border-color: #65dfc1; }
    """)
    directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    directory.mkdir(parents=True, exist_ok=True)
    window = Dashboard(directory / "pivluma.sqlite3")
    application.setWindowIcon(window.windowIcon())
    settings = QSettings("Pivluma", "Pivluma")
    index = window.language_picker.findData(settings.value("language", "en"))
    window.language_picker.setCurrentIndex(max(0, index))
    window.language_picker.currentIndexChanged.connect(
        lambda: settings.setValue("language", window.language_picker.currentData())
    )
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        updates = directory / "Versions"
        preferred = UpdateController.preferred_executable(settings, updates)
        if preferred is not None:
            started, _ = QProcess.startDetached(str(preferred), [], str(preferred.parent))
            if started:
                return 0
        window.update_button.show()
        window.update_label.show()
        UpdateController(window, window.update_button, updates, settings)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
