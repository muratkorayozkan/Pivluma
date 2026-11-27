"""Offline market-analysis dashboard."""

import csv
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QPointF, QProcess, QRectF, QSettings, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QIcon, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from pivluma import __version__
from pivluma.analyst import answer
from pivluma.appearance import THEMES, colors_for, stylesheet
from pivluma.data import PriceStore, read_prices, sample_prices
from pivluma.dialogs import about_dialog, axis_dialog, history_dialog, settings_dialog
from pivluma.forecast import TRAINING_WINDOW, analyze
from pivluma.localization import translate
from pivluma.market import PUBLIC_SYMBOLS, MarketFeed
from pivluma.update_ui import UpdateController


MAX_HORIZON = 120


class PriceChart(QWidget):
    selected = Signal(object)
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(280)
        self.history = []
        self.days = []
        self.history_limit = 120
        self.axis_limits = None
        self.selected_index = None
        self.plot_area = QRectF()
        self.colors = colors_for("dark-blue", Qt.ColorScheme.Dark)
        self.result = None
        self.language = "en"
        self.setAccessibleName("Closing prices and forecast interval")

    def visible_history(self):
        return self.history[-self.history_limit:] if self.history_limit else self.history

    def price_bounds(self):
        history = self.visible_history()
        lower = min(history + list(self.result.lower))
        upper = max(history + list(self.result.upper))
        padding = (upper - lower) * .08 or max(upper * .01, .001)
        return lower - padding, upper + padding

    def set_history_limit(self, limit):
        self.history_limit = limit
        self.select_index(None)

    def set_axis(self, minimum=None, maximum=None):
        self.axis_limits = None if minimum is None else (minimum, maximum)
        self.update()

    def select_index(self, index):
        self.selected_index = index
        self.selected.emit(index)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.plot_area.contains(event.position()):
            count = len(self.visible_history()) + len(self.result.forecast)
            fraction = (event.position().x() - self.plot_area.left()) / self.plot_area.width()
            self.select_index(round(fraction * (count - 1)))
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        if not self.history:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _, surface, text, grid, actual_color, forecast_color, fill = self.colors
        painter.fillRect(self.rect(), QColor(surface))
        history = self.visible_history()
        result = self.result
        lower, upper = self.axis_limits or self.price_bounds()
        values = [lower + (upper - lower) * index / 4 for index in range(5)]
        labels = [f"{value:.2f}" if abs(value) < 1_000_000 else f"{value:.3g}" for value in values]
        font = QFontMetricsF(painter.font())
        left_margin = max(font.horizontalAdvance(label) for label in labels) + 24
        top_margin = font.height() + 8
        bottom_margin = font.height() * 2 + 12
        chart = QRectF(left_margin, top_margin, self.width() - left_margin - 24,
                       self.height() - top_margin - bottom_margin)
        self.plot_area = chart
        last = len(history) + len(result.forecast) - 1

        def point(index, price):
            return QPointF(chart.left() + chart.width() * index / last,
                           chart.bottom() - chart.height() * (price - lower) / (upper - lower))

        for value, label in zip(values, labels):
            left, right = point(0, value), point(last, value)
            painter.setPen(QColor(grid))
            painter.drawLine(left, right)
            painter.setPen(QColor(text))
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
        painter.setBrush(QColor(fill))
        painter.drawPolygon(QPolygonF(top + list(reversed(bottom))))
        painter.setPen(QPen(QColor(actual_color), 2))
        painter.drawPolyline(QPolygonF([point(i, value) for i, value in enumerate(history)]))
        painter.setPen(QPen(QColor(forecast_color), 2, Qt.PenStyle.DashLine))
        painter.drawPolyline(QPolygonF([
            point(start + i, value) for i, value in enumerate([history[-1], *result.forecast])
        ]))
        if self.selected_index is not None:
            index = self.selected_index
            price = history[index] if index < len(history) else result.forecast[index - len(history)]
            location = point(index, price)
            painter.setPen(QPen(QColor(text), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(location.x(), chart.top()), QPointF(location.x(), chart.bottom()))
            painter.setBrush(QColor(actual_color if index < len(history) else forecast_color))
            painter.drawEllipse(location, 4, 4)
        painter.restore()
        painter.setPen(QColor(text))
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
        self.theme = "dark-blue"
        self.settings = None
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
        navigation = QHBoxLayout()
        navigation.addStretch()
        for label, action in (("Revision history", history_dialog), ("About Pivluma", about_dialog),
                              ("Settings", settings_dialog)):
            button = self.localized_button(label)
            button.clicked.connect(lambda checked=False, callback=action: callback(self))
            navigation.addWidget(button)
        layout.addLayout(header)
        layout.addLayout(navigation)
        self.update_message = ""
        self.update_label = QLabel()
        self.update_label.setWordWrap(True)
        self.update_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.update_label.setFixedWidth(310)
        self.update_label.hide()
        update_row = QHBoxLayout()
        update_row.addStretch()
        update_column = QVBoxLayout()
        update_column.addWidget(self.update_button, alignment=Qt.AlignmentFlag.AlignRight)
        update_column.addWidget(self.update_label, alignment=Qt.AlignmentFlag.AlignRight)
        update_row.addLayout(update_column)
        layout.addLayout(update_row)
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
        self.horizon.setRange(1, MAX_HORIZON)
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
        chart_controls = QHBoxLayout()
        chart_controls.addWidget(self.localized_label("Chart history"))
        self.history_range = QComboBox()
        for limit, message in ((30, "30 observations"), (60, "60 observations"),
                               (120, "120 observations"), (0, "All observations")):
            index = self.history_range.count()
            self.history_range.addItem(self.text(message), limit)
            self.localize(lambda text, index=index: self.history_range.setItemText(index, text), message)
        self.history_range.setCurrentIndex(2)
        chart_controls.addWidget(self.history_range)
        axis = self.localized_button("Price axis")
        axis.clicked.connect(lambda: axis_dialog(self))
        chart_controls.addWidget(axis)
        chart_controls.addStretch()
        layout.addLayout(chart_controls)
        self.chart = PriceChart()
        self.history_range.currentIndexChanged.connect(
            lambda: self.chart.set_history_limit(self.history_range.currentData())
        )
        layout.addWidget(self.chart, 1)
        self.point_details = QLabel()
        self.point_details.setWordWrap(True)
        self.point_details.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.point_details)
        self.chart.selected.connect(self.show_chart_point)
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
        self.change_theme(self.theme)

    def change_theme(self, theme):
        self.theme = theme if theme in THEMES else "dark-blue"
        colors = colors_for(self.theme, QApplication.styleHints().colorScheme())
        self.setStyleSheet(stylesheet(colors))
        self.chart.colors = colors
        self.chart.update()
        if self.settings is not None:
            self.settings.setValue("theme", self.theme)

    def show_chart_point(self, index):
        if index is None:
            self.point_details.setText(self.text("Click the chart to inspect a price or forecast."))
            return
        visible = len(self.chart.visible_history())
        if index < visible:
            day, close = self.chart.days[index - visible], self.chart.visible_history()[index]
            text = self.text("{date} · Close {price:.8g}", date=day, price=close)
        else:
            step = index - visible
            result = self.chart.result
            text = self.text(
                "Forecast step {step} · {price:.8g} · Interval [{lower:.8g}, {upper:.8g}]",
                step=step + 1, price=result.forecast[step], lower=result.lower[step],
                upper=result.upper[step],
            )
        self.point_details.setText(text)

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
        self.show_chart_point(self.chart.selected_index)
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
                                 f"{timestamp.astimezone():%Y-%m-%d %H:%M:%S UTC%z}{note}")

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
        self.chart.set_axis()
        self.horizon.blockSignals(True)
        self.horizon.setMaximum(min(MAX_HORIZON, len(prices) - TRAINING_WINDOW - 9))
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
        self.chart.days = [price.day for price in self.prices]
        self.chart.result = result
        self.chart.select_index(None)
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
    directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    directory.mkdir(parents=True, exist_ok=True)
    window = Dashboard(directory / "pivluma.sqlite3")
    application.setWindowIcon(window.windowIcon())
    settings = QSettings("Pivluma", "Pivluma")
    window.settings = settings
    window.change_theme(settings.value("theme", "dark-blue", type=str))
    application.styleHints().colorSchemeChanged.connect(
        lambda *_: window.change_theme(window.theme) if window.theme == "system" else None
    )
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
