"""Offline market-analysis dashboard."""

import csv
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from pivluma import __version__
from pivluma.analyst import answer
from pivluma.appearance import THEMES, colors_for, stylesheet
from pivluma.chart import PriceChart
from pivluma.data import PriceStore, read_prices, sample_prices
from pivluma.dialogs import about_dialog, axis_dialog, history_dialog, settings_dialog
from pivluma.forecast import TRAINING_WINDOW, analyze
from pivluma.local_model import MODELS, LocalModel
from pivluma.localization import translate
from pivluma.market import PUBLIC_SYMBOLS, MarketFeed
from pivluma.update_ui import UpdateController


MAX_HORIZON = 120


class Dashboard(QMainWindow):
    def __init__(self, database: Path):
        super().__init__()
        self.database = database
        self.language = "en"
        self.theme = "dark-blue"
        self.settings = None
        self.analyst_model = ""
        self.local_model = LocalModel(self)
        self.local_model.completed.connect(self.show_model_answer)
        self.localized = []
        self.market_message = "Twelve Data · Disconnected"
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("pivluma.ico"))))
        self.setWindowTitle(f"Pivluma {__version__}")
        self.setMinimumSize(760, 680)
        self.resize(1280, 900)
        page = QWidget()
        self.scroll_area = QScrollArea()
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(page)
        self.setCentralWidget(self.scroll_area)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(10)
        title = QLabel("Pivluma")
        title.setObjectName("title")
        header = QHBoxLayout()
        layout.addWidget(title)
        header.addStretch()
        header.addWidget(self.localized_label("Language"))
        self.language_picker = QComboBox()
        self.language_picker.addItem("EN · English", "en")
        self.language_picker.addItem("TR · Türkçe", "tr")
        self.language_picker.currentIndexChanged.connect(self.change_language)
        header.addWidget(self.language_picker)
        self.update_button = self.localized_button("Check for updates")
        self.update_button.hide()
        for label, action in (("Revision history", history_dialog), ("About Pivluma", about_dialog),
                              ("Settings", settings_dialog)):
            button = self.localized_button(label)
            button.clicked.connect(lambda checked=False, callback=action: callback(self))
            header.addWidget(button)
        header.addWidget(self.update_button)
        layout.addLayout(header)
        self.update_message = ""
        self.update_label = QLabel()
        self.update_label.setWordWrap(True)
        self.update_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.update_label.setFixedWidth(310)
        self.update_label.hide()
        update_row = QHBoxLayout()
        update_row.addStretch()
        update_row.addWidget(self.update_label, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addLayout(update_row)
        market_section = QVBoxLayout()
        market_heading = self.localized_label("Market data")
        market_heading.setProperty("sectionTitle", True)
        market_section.addWidget(market_heading)
        layout.addLayout(market_section)
        toolbar = QHBoxLayout()
        market_section.addLayout(toolbar)
        self.symbol_picker = QComboBox()
        self.symbol_picker.setEditable(True)
        self.symbol_picker.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.symbol = self.symbol_picker.lineEdit()
        instruments = {
            **PUBLIC_SYMBOLS,
            "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "TSLA": "Tesla",
            "SPY": "SPDR S&P 500 ETF", "EUR/USD": "Euro / US dollar",
            "USD/TRY": "US dollar / Turkish lira",
        }
        for code, name in instruments.items():
            index = self.symbol_picker.count()
            self.symbol_picker.addItem(f"{self.text(name)} ({code})", code)
            self.localize(
                lambda text, index=index, code=code: self.symbol_picker.setItemText(index, f"{text} ({code})"),
                name,
            )
        self.symbol_picker.setCurrentIndex(-1)
        self.symbol_picker.activated.connect(
            lambda index: self.symbol.setText(self.symbol_picker.itemData(index))
        )
        self.localize(self.symbol.setPlaceholderText, "Symbol")
        self.localize(self.symbol.setAccessibleName, "Symbol")
        toolbar.addWidget(self.symbol_picker, 1)
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
        self.workspace_stack = QStackedWidget()
        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_tabs = QTabWidget()
        self.workspace_stack.addWidget(self.workspace_splitter)
        self.workspace_stack.addWidget(self.workspace_tabs)
        layout.addWidget(self.workspace_stack, 1)
        self.chart_panel = QWidget()
        chart_layout = QVBoxLayout(self.chart_panel)
        chart_layout.setContentsMargins(3, 3, 3, 3)
        self.analyst_panel = QWidget()
        analyst_layout = QVBoxLayout(self.analyst_panel)
        analyst_layout.setContentsMargins(8, 3, 3, 3)

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
        reset = self.localized_button("Reset chart")
        reset.clicked.connect(self.reset_chart)
        chart_controls.addWidget(reset)
        chart_controls.addStretch()
        chart_layout.addLayout(chart_controls)
        self.chart = PriceChart()
        self.history_range.currentIndexChanged.connect(
            lambda: self.chart.set_history_limit(self.history_range.currentData())
        )
        layers = QHBoxLayout()
        for key, title in (("close", "Close"), ("average", "SMA 20"),
                           ("forecast", "Forecast"), ("interval", "Interval")):
            control = QCheckBox()
            self.localize(control.setText, title)
            control.setChecked(self.chart.layers[key])
            control.toggled.connect(lambda enabled, key=key: self.chart.set_layer(key, enabled))
            layers.addWidget(control)
        layers.addStretch()
        chart_layout.addLayout(layers)
        chart_layout.addWidget(self.chart, 1)
        self.point_details = QLabel()
        self.point_details.setWordWrap(True)
        self.point_details.setTextFormat(Qt.TextFormat.PlainText)
        chart_layout.addWidget(self.point_details)
        self.chart.selected.connect(self.show_chart_point)
        self.chart_legend = self.localized_label(
            "Closing price  /  Dashed forecast  /  Shaded empirical interval"
        )
        self.chart_legend.setWordWrap(True)
        chart_layout.addWidget(self.chart_legend)
        self.metrics = QLabel()
        self.metrics.setWordWrap(True)
        self.metrics.setObjectName("metrics")
        chart_layout.addWidget(self.metrics)
        export = self.localized_button("Export forecast CSV")
        export.clicked.connect(self.export_forecast)
        forecast_controls.addWidget(export)
        analyst_title = self.localized_label("Local analyst")
        analyst_title.setProperty("sectionTitle", True)
        analyst_layout.addWidget(analyst_title)
        prompts = QGridLayout()
        for index, (label, question) in enumerate((
            ("Price movement", "How did the price move?"),
            ("Forecast", "What is the forecast?"),
            ("Moving average", "What is the moving average?"),
            ("Backtest", "How accurate was the model?"),
            ("Visible period", "Summarize the visible period"),
            ("Selected point", "Explain the selected point"),
        )):
            topic = self.localized_button(label)
            topic.clicked.connect(lambda checked=False, prompt=question: self.ask_topic(prompt))
            prompts.addWidget(topic, index // 2, index % 2)
        analyst_layout.addLayout(prompts)
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
        analyst_layout.addLayout(analyst_controls)
        self.reply = QLabel()
        self.reply.setTextFormat(Qt.TextFormat.PlainText)
        self.reply.setWordWrap(True)
        self.reply.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        analyst_layout.addWidget(self.reply)
        analyst_layout.addStretch()
        self.chart.viewport_changed.connect(self.refresh_contextual_reply)
        self.chart.selected.connect(lambda _: self.refresh_contextual_reply())
        self.arrange_workspace()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        market_controls = QHBoxLayout()
        connect = self.localized_button("Connect live")
        connect.clicked.connect(self.connect_market)
        disconnect = self.localized_button("Disconnect")
        disconnect.clicked.connect(self.stop_market)
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "workspace_stack"):
            self.arrange_workspace()

    def arrange_workspace(self):
        wide = self.width() >= 1180
        if wide and self.workspace_splitter.count() != 2:
            while self.workspace_tabs.count():
                self.workspace_tabs.removeTab(0)
            self.workspace_splitter.addWidget(self.chart_panel)
            self.workspace_splitter.addWidget(self.analyst_panel)
            self.chart_panel.show()
            self.analyst_panel.show()
            self.workspace_splitter.setStretchFactor(0, 3)
            self.workspace_splitter.setStretchFactor(1, 2)
            self.workspace_splitter.setSizes([730, 440])
        elif not wide and self.workspace_tabs.count() != 2:
            self.workspace_tabs.addTab(self.chart_panel, self.text("Chart"))
            self.workspace_tabs.addTab(self.analyst_panel, self.text("Local analyst"))
        self.workspace_stack.setCurrentWidget(self.workspace_splitter if wide else self.workspace_tabs)

    def reset_chart(self):
        self.history_range.setCurrentIndex(2)
        self.chart.reset_view()

    def change_theme(self, theme):
        self.theme = theme if theme in THEMES else "dark-blue"
        colors = colors_for(self.theme, QApplication.styleHints().colorScheme())
        self.setStyleSheet(stylesheet(colors))
        self.chart.colors = colors
        self.chart.update()
        if self.settings is not None:
            self.settings.setValue("theme", self.theme)

    def show_chart_point(self, index):
        self.point_details.setText(
            self.text("Click the chart to inspect a price or forecast.")
            if index is None else self.chart.point_text(index)
        )

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
        symbol = self.symbol.text()
        for setter, message in self.localized:
            setter(self.text(message))
        self.symbol.setText(symbol)
        if self.workspace_tabs.count() == 2:
            self.workspace_tabs.setTabText(0, self.text("Chart"))
            self.workspace_tabs.setTabText(1, self.text("Local analyst"))
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
        self.local_model.cancel()
        if self.reply.text():
            self.render_analyst(False)
        self.status.clear()

    def set_market_status(self, message):
        self.market_message = message
        self.market_status.setText(self.text(message))

    def set_history_warning(self, message):
        self.history_message = message
        self.history_warning.setText(self.text(message))
        self.history_warning.setVisible(bool(message))

    def connect_market(self):
        try:
            symbol = self.symbol.text().strip().upper()
            if not symbol:
                self.symbol_picker.showPopup()
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
            self.quote_label.hide()
            return
        price, timestamp = self.last_quote
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        note = f" · {self.text('Last received quote')}" if self.stream_lost or age >= 120 else ""
        self.quote_label.show()
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
        self.chart.history_offset = 0
        self.horizon.blockSignals(True)
        self.horizon.setMaximum(min(MAX_HORIZON, len(prices) - TRAINING_WINDOW - 9))
        self.horizon.blockSignals(False)
        self.refresh()

    def ask_topic(self, prompt):
        self.question.setText(self.text(prompt))
        self.ask_analyst()

    def contextual_topic(self):
        question = self.question.text().casefold()
        if any(term in question for term in ("selected point", "seçili nokta", "seçilen nokta")):
            return "point"
        if any(term in question for term in ("visible period", "görünür dönem", "grafikteki dönem")):
            return "period"
        return None

    def refresh_contextual_reply(self):
        if self.reply.text() and self.contextual_topic() is not None:
            self.local_model.cancel()
            self.render_analyst(False)

    def set_analyst_model(self, model):
        self.local_model.cancel()
        self.analyst_model = model if model in MODELS else ""
        if self.settings is not None:
            self.settings.setValue("analyst_model", self.analyst_model)

    def ask_analyst(self):
        self.local_model.cancel()
        self.render_analyst(True)

    def render_analyst(self, use_model):
        topic = self.contextual_topic()
        if topic == "point":
            index = self.chart.selected_index
            message = self.text("Select a point on the chart first.")
            if index is not None:
                message = f"{self.series_label}\n{self.chart.point_text(index)}"
                if index >= len(self.chart.visible_history()):
                    message += "\n" + self.text("The interval is based on past forecast errors.")
            self.reply.setText(message)
            if index is not None:
                self.request_model_answer(use_model, message)
            return
        prices = self.prices
        question = self.question.text()
        if topic == "period":
            start, end = self.chart.bounds()
            prices = prices[start:end]
            question = "How did the price move?"
        message = answer(question, self.series_label, prices, self.chart.result, self.language)
        self.reply.setText(message)
        self.request_model_answer(use_model, message)

    def request_model_answer(self, use_model, calculation):
        question = self.question.text().strip()
        if not use_model or not self.analyst_model or not question:
            return
        result = self.chart.result
        first, last = self.prices[0], self.prices[-1]
        facts = {
            "series": self.series_label,
            "first_date": str(first.day), "last_date": str(last.day), "last_close": last.close,
            "selected_forecast_model": result.model, "horizon": len(result.forecast),
            "forecast": [
                {"step": step, "price": estimate, "lower": lower, "upper": upper}
                for step, (estimate, lower, upper) in enumerate(
                    zip(result.forecast, result.lower, result.upper), 1
                )
            ],
            "strategy_mae": result.mae, "last_price_baseline_mae": result.baseline_mae,
            "backtest_origins": result.origins, "calculation": calculation,
        }
        self.model_calculation = calculation
        self.reply.setText(calculation + "\n\n" + self.text("Local model is preparing an explanation…"))
        self.local_model.start(self.analyst_model, self.language, question[:2000], facts)

    def show_model_answer(self, model, response):
        explanation = (
            self.text("Local model ({model})", model=model) + "\n" + response
            if response else self.text("Local model unavailable. Check that Ollama and the selected model are running.")
        )
        self.reply.setText(self.model_calculation + "\n\n" + explanation)

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
        self.local_model.cancel()
        closes = [price.close for price in self.prices]
        result = analyze(closes, self.horizon.value())
        self.chart.history = closes
        self.chart.days = [price.day for price in self.prices]
        self.chart.result = result
        self.chart.hover_index = None
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
            ("Selected model", self.text(result.model)),
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
    window.set_analyst_model(settings.value("analyst_model", "", type=str))
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
