"""A symbol's news, saved forecast results, and historical market playback."""
import json
import sqlite3
from contextlib import closing
from datetime import timedelta

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHeaderView, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from pivluma.chart import PriceChart
from pivluma.forecast import TRAINING_WINDOW, analyze
from pivluma.headlines import HeadlineFeed
from pivluma.lab import ForecastStore
from pivluma.llama_forecast import forecast_input, forecast_path
from pivluma.local_assets import ready
from pivluma.local_model import LocalModel
from pivluma.news import align_scores
from pivluma.research_store import ResearchStore, performance_rows


class ResearchWorkspace(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.text = window, window.text
        self.prices = tuple(window.prices)
        self.symbol, self.database = window.series_symbol, window.database
        self.articles = []
        self.replay_actual = ()
        self.replay_request = None
        self.model = LocalModel(window.local_model.root, self)
        self.model.completed.connect(self.receive_replay)
        self.headlines = HeadlineFeed(self)
        self.headlines.loaded.connect(self.show_articles)
        self.headlines.failed.connect(lambda message: self.news_status.setText(self.text(message)))
        self.resize(1140, 850)
        self.setWindowTitle(self.text("Research workspace"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(window.series_label))
        tabs = QTabWidget()
        layout.addWidget(tabs)

        research = self.page(tabs, "Research")
        self.summary = self.label(research, "No saved llama.cpp forecast for this symbol.")
        self.forecast_context = self.label(research, "No input snapshot was saved for this forecast.")
        self.current_chart = self.chart(research)
        self.show_history(self.current_chart, self.prices)
        if window.chart.llama_forecast:
            self.current_chart.llama_forecast = tuple(window.chart.llama_forecast)
        self.news_status = self.label(research, "Headlines show when GDELT first saw each article.")
        news_controls = QHBoxLayout()
        research.addLayout(news_controls)
        self.button(news_controls, "Load headlines", self.load_headlines).setEnabled(bool(self.symbol))
        self.open_article = self.button(news_controls, "Open source article", self.open_selected)
        self.open_article.setEnabled(False)
        news_controls.addStretch()
        self.news_table = self.table(research, ("Headline", "Source", "First seen (local time)"), stretch_column=0)
        self.news_table.currentCellChanged.connect(self.select_article)
        self.news_table.cellDoubleClicked.connect(lambda *_: self.open_selected())

        performance = self.page(tabs, "Model performance")
        self.performance_status = self.label(performance, "Completed, prospectively recorded llama.cpp forecasts")
        self.performance = self.table(performance, (
            "Forecast steps", "Samples", "llama.cpp MAE", "Flat-price MAE", "Direction accuracy", "Saved band coverage"
        ))

        playback = self.page(tabs, "Market replay")
        self.label(playback, "Replay uses only closes through the selected date and news scores archived before it.")
        controls = QHBoxLayout()
        playback.addLayout(controls)
        controls.addWidget(QLabel(self.text("Historical date")))
        self.origin = QComboBox()
        controls.addWidget(self.origin)
        for count in range(TRAINING_WINDOW + 10, len(self.prices)):
            self.origin.addItem(self.prices[count - 1].day.isoformat(), count)
        self.origin.setCurrentIndex(self.origin.count() - 1)
        controls.addWidget(QLabel(self.text("Forecast steps")))
        self.horizon = QSpinBox()
        controls.addWidget(self.horizon)
        self.horizon.setRange(1, 120)
        self.engine = QComboBox()
        self.engine.addItem(self.text("Statistical reference"), False)
        if ready(self.model.root):
            self.engine.addItem("llama.cpp", True)
            self.engine.setCurrentIndex(1)
        controls.addWidget(self.engine)
        self.run = self.button(controls, "Generate replay forecast", self.generate_replay)
        self.advance = self.button(controls, "Reveal next close", self.advance_replay)
        self.advance.setEnabled(False)
        self.replay_status = self.label(playback, "Select a historical date and generate a forecast.")
        self.replay_chart = self.chart(playback)
        self.replay_chart.replay_mode = True
        self.label(playback, "Dash-dot line: forecast · Dotted line: revealed closes")
        self.steps = self.table(playback, ("Step", "Forecast", "Date", "Actual", "Absolute error"))
        self.origin.currentIndexChanged.connect(self.origin_changed)
        self.origin_changed()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(self.text("Close"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.load_journal()

    def page(self, tabs, title):
        page = QWidget()
        tabs.addTab(page, self.text(title))
        return QVBoxLayout(page)

    def label(self, layout, message):
        label = QLabel(self.text(message))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    def button(self, layout, message, action):
        button = QPushButton(self.text(message))
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    def chart(self, layout):
        chart = PriceChart()
        chart.colors = self.window.chart.colors
        chart.language = self.window.language
        chart.set_layer("llama_band", False)
        chart.setMinimumHeight(260)
        layout.addWidget(chart, 2)
        return chart

    def table(self, layout, columns, stretch_column=None):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels([self.text(column) for column in columns])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        if stretch_column is not None:
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.verticalHeader().hide()
        layout.addWidget(table, 1)
        return table

    @staticmethod
    def fill(table, rows):
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                cell = QTableWidgetItem(f"{value:.5g}" if isinstance(value, float) else str(value))
                cell.setToolTip(cell.text())
                table.setItem(row, column, cell)

    @staticmethod
    def show_history(chart, prices):
        chart.history = [price.close for price in prices]
        chart.days = [price.day.isoformat() for price in prices]
        chart.history_offset = 0
        chart.select_index(None)
        chart.view_changed()

    def load_journal(self):
        if not self.symbol or not self.database.is_file():
            self.performance_status.setText(self.text("No completed prospective forecasts."))
            return
        try:
            with closing(sqlite3.connect(self.database)) as connection:
                store = ForecastStore(connection)
                runs = [run for run in store.runs(self.symbol) if run[1] == "llama.cpp"]
                stats = performance_rows(connection, self.symbol)
                row = connection.execute(
                    "SELECT research_context FROM forecast_runs WHERE symbol = ? AND engine = ? "
                    "ORDER BY origin DESC, id DESC LIMIT 1", (self.symbol, "llama.cpp")
                ).fetchone()
                context = json.loads(row[0]) if row and row[0] else None
        except (sqlite3.Error, ValueError):
            self.performance_status.setText(self.text("Forecast journal unavailable."))
            return
        if runs:
            day, _, path, actual = runs[0]
            final = self.text("Pending") if len(actual) < len(path) else f"{actual[-1][1]:.8g}"
            message = self.text("Latest saved forecast · {date} · {steps} steps · Final {price:.8g} · Actual {actual}",
                                date=day, steps=len(path), price=path[-1], actual=final)
            previous = next((prior for prior in runs[1:] if prior[0] < day and len(prior[2]) == len(path)), None)
            if previous is not None:
                message += " · " + self.text("Change vs. {date}: {change:+.8g}",
                                              date=previous[0], change=path[-1] - previous[2][-1])
            self.summary.setText(message)
        if context:
            self.forecast_context.setText(self.text(
                "Saved inputs · Five-close move {move:+.2f}% · RSI {rsi} · {count} news scores · {source}",
                move=context["five_close_move"],
                rsi=f'{context["rsi"]:.2f}' if context["rsi"] is not None else self.text("Unavailable"),
                count=context["scores"], source=self.text(context["source"] or "Prices only")
            ))
        if not stats:
            self.performance_status.setText(self.text("No completed prospective forecasts."))
        self.fill(self.performance, [
            (steps, samples, model, flat, f"{direction / samples:.0%} ({direction}/{samples})",
             f"{covered / bands:.0%} ({covered}/{bands})" if bands else self.text("Unavailable"))
            for steps, samples, model, flat, direction, covered, bands in stats
        ])

    def load_headlines(self):
        self.news_status.setText(self.text("Loading headlines…"))
        self.headlines.start(self.symbol)

    def show_articles(self, symbol, articles):
        if symbol != self.symbol:
            return
        self.articles = articles
        self.fill(self.news_table, [(article.title, article.source, article.seen.astimezone().strftime("%Y-%m-%d %H:%M"))
                                    for article in articles])
        self.news_status.setText(self.text("Headlines show when GDELT first saw each article." if articles else
                                           "No recent headlines were returned."))
        if articles:
            self.news_table.setCurrentCell(0, 0)
            self.select_article(0)

    def select_article(self, row, *_):
        self.open_article.setEnabled(0 <= row < len(self.articles))
        if not self.open_article.isEnabled():
            return
        stamp = self.articles[row].seen.date().isoformat()
        chart = self.current_chart
        indices = [index for index, day in enumerate(chart.days)
                   if day <= stamp and self.articles[row].seen.date() - self.prices[index].day <= timedelta(days=7)]
        if indices:
            index = indices[-1]
            chart.pan_history(len(chart.history) - 1 - index)
            chart.select_index(chart.visible_days().index(chart.days[index]))
            self.news_status.setText(chart.point_text(chart.selected_index))
        else:
            chart.select_index(None)
            self.news_status.setText(self.text("No nearby close in this price series."))

    def open_selected(self):
        if 0 <= (row := self.news_table.currentRow()) < len(self.articles):
            QDesktopServices.openUrl(QUrl(self.articles[row].url))

    def origin_changed(self):
        count = self.origin.currentData()
        self.run.setEnabled(count is not None)
        if count is not None:
            self.horizon.setMaximum(min(120, len(self.prices) - count, count - TRAINING_WINDOW - 9))
            self.horizon.setValue(min(self.window.horizon.value(), self.horizon.maximum()))
            self.replay_chart.llama_forecast = ()
            self.replay_chart.replay_closes = ()
            self.show_history(self.replay_chart, self.prices[:count])
        self.advance.setEnabled(False)
        self.fill(self.steps, [])
        self.replay_status.setText(self.text("Select a historical date and generate a forecast."))

    def generate_replay(self):
        count = self.origin.currentData()
        if count is None:
            return
        horizon = self.horizon.value()
        prices = self.prices[:count]
        self.replay_actual = self.prices[count:count + horizon]
        source, scores = "", {}
        if self.symbol and self.database.is_file():
            try:
                with closing(sqlite3.connect(self.database)) as connection:
                    source, scores = ResearchStore(connection).scores_at(self.symbol, prices[-1].day)
            except (sqlite3.Error, ValueError):
                self.replay_status.setText(self.text("Forecast journal unavailable."))
                return
        self.replay_chart.llama_forecast = ()
        self.replay_chart.replay_closes = ()
        self.show_history(self.replay_chart, prices)
        self.advance.setEnabled(False)
        self.fill(self.steps, [])
        self.replay_message = (self.text("Archived news scores · {source}", source=self.text(source)) if scores
                               else self.text("No news snapshot was archived before this date. Using prices only."))
        if self.engine.currentData():
            facts = forecast_input(prices, horizon, scores, source)
            self.replay_request = prices[-1].close, horizon, facts
            self.set_busy(True)
            self.replay_status.setText(self.text("Running local model…"))
            self.model.start_forecast(facts)
        else:
            self.show_replay(analyze([price.close for price in prices], horizon,
                                     align_scores(prices, scores) if scores else None).forecast)

    def set_busy(self, busy):
        for widget in (self.run, self.origin, self.horizon, self.engine):
            widget.setEnabled(not busy)

    def receive_replay(self, _task, response):
        if self.replay_request is None:
            return
        close, horizon, facts = self.replay_request
        self.replay_request = None
        self.set_busy(False)
        try:
            self.show_replay(forecast_path(response, close, horizon,
                                          facts["research_evidence"]["bounded_adjustment_percent"],
                                          facts["historical_patterns"]))
        except (ValueError, OverflowError):
            self.replay_status.setText(self.text("llama.cpp forecast unavailable."))

    def show_replay(self, path):
        self.replay_chart.llama_forecast = tuple(path)
        self.replay_chart.update()
        self.replay_status.setText(self.replay_message)
        self.advance.setEnabled(True)
        self.update_steps()

    def advance_replay(self):
        chart = self.replay_chart
        count = len(chart.replay_closes)
        if count >= len(self.replay_actual):
            return
        chart.replay_closes = (*chart.replay_closes, self.replay_actual[count].close)
        chart.update()
        self.advance.setEnabled(count + 1 < len(self.replay_actual))
        self.update_steps()

    def update_steps(self):
        chart = self.replay_chart
        pending = self.text("Pending")
        self.fill(self.steps, [
            (index + 1, estimate, self.replay_actual[index].day, chart.replay_closes[index],
             abs(estimate - chart.replay_closes[index]))
            if index < len(chart.replay_closes) else (index + 1, estimate, pending, pending, pending)
            for index, estimate in enumerate(chart.llama_forecast)
        ])

    def done(self, result):
        self.replay_request = None
        self.model.stop_engine()
        super().done(result)
