"""Forecast Lab tables and asynchronous local-model comparisons."""
import sqlite3
from contextlib import closing

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from pivluma.lab import ForecastStore, paired_input_errors, replay, replay_origins, terminal_mae
from pivluma.llama_forecast import forecast_input, forecast_path
from pivluma.local_assets import ready
from pivluma.local_model import LocalModel
from pivluma.news import align_scores


VARIANTS = (
    (False, False, "Prices only"),
    (True, False, "Prices and news"),
    (False, True, "Prices and indicators"),
    (True, True, "Prices, news, and indicators"),
)


class ReplayThread(QThread):
    rows_ready = Signal(object)

    def __init__(self, prices, horizon, scores, parent):
        super().__init__(parent)
        self.prices, self.horizon, self.scores = prices, horizon, scores

    def run(self):
        self.rows_ready.emit(replay(self.prices, self.horizon, self.scores, align_scores))


class ForecastLab(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.text = window.text
        self.database = window.database
        self.symbol = window.series_symbol or "Synthetic sample"
        self.prices = tuple(window.prices)
        self.horizon = window.horizon.value()
        self.scores = dict(window.news_scores)
        self.source = window.news_source
        self.comparison_symbol = window.chart.comparison_symbol
        self.comparison = dict(window.chart.comparison_closes)
        self.result = window.chart.result
        self.llama_path = window.chart.llama_forecast
        self.model = LocalModel(window.local_model.root, self)
        self.model.completed.connect(self.model_result)
        self.jobs = []
        self.job = None
        self.local_rows = []
        self.results = []
        self.paired_outcomes = {}
        self.setWindowTitle(self.text("Forecast Lab"))
        available = window.screen().availableGeometry()
        self.resize(min(960, available.width() - 30), min(630, available.height() - 50))
        self.setMinimumSize(590, 400)
        layout = QVBoxLayout(self)
        title = QLabel(self.text("{symbol} · {steps} observation steps", symbol=self.text(self.symbol),
                                 steps=self.horizon))
        title.setProperty("sectionTitle", True)
        layout.addWidget(title)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        backtest = self.page(tabs, "Walk-forward test")
        self.backtest_status = self.note(backtest, "Replaying the statistical models on older observations…")
        self.backtest = self.table(backtest, ("Origin", "Actual close", "Flat price", "Without news", "With news"))
        self.local_button = QPushButton(self.text("Test llama on recent origins"))
        self.local_button.setEnabled(ready(self.model.root) and bool(replay_origins(self.prices, self.horizon)))
        self.local_button.clicked.connect(self.run_local_replay)
        backtest.addWidget(self.local_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.local_status = self.note(backtest, "Local replay uses up to three completed origins and may take a few minutes.")
        self.local_table = self.table(backtest, ("Origin", "Actual close", "llama.cpp", "Absolute error"))

        inputs = self.page(tabs, "Input contribution")
        self.input_note = self.note(
            inputs, "Compare model inputs on the same data. A change in the estimate does not measure improvement."
        )
        self.input_button = QPushButton(self.text("Measure current input contribution"))
        self.input_button.setEnabled(ready(self.model.root))
        self.input_button.clicked.connect(self.run_contribution)
        inputs.addWidget(self.input_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.input_table = self.table(inputs, ("Inputs", "Final forecast", "Change vs. prices only"))
        self.paired_button = QPushButton(self.text("Replay all input combinations"))
        self.paired_button.setEnabled(self.has_replay())
        self.paired_button.clicked.connect(self.run_paired_replay)
        inputs.addWidget(self.paired_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.paired_status = self.note(inputs, "Replays up to three origins. Errors use only origins shared by all combinations.")
        self.paired_table = self.table(inputs, ("Inputs", "Final-step MAE", "MAE change vs. prices only"))

        history = self.page(tabs, "Forecast journal")
        self.history_note = self.note(
            history, "Saved runs are matched to subsequent closing observations. Pending runs mature as new closes arrive."
        )
        actions = QHBoxLayout()
        self.save = QPushButton(self.text("Save current forecast"))
        self.save.clicked.connect(self.save_current)
        actions.addWidget(self.save)
        actions.addStretch()
        history.addLayout(actions)
        self.history_table = self.table(history, ("Origin", "Model", "Steps", "Final forecast", "Final actual", "Absolute error"))
        self.steps_table = self.table(history, ("Step", "Forecast", "Observed date", "Actual close", "Absolute error"))
        self.saved_runs = []
        self.history_table.currentCellChanged.connect(self.show_saved_steps)
        self.load_history()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.worker = ReplayThread(self.prices, self.horizon, self.scores, self)
        self.worker.rows_ready.connect(self.show_replay)
        self.worker.start()

    def page(self, tabs, label):
        page = QWidget()
        layout = QVBoxLayout(page)
        tabs.addTab(page, self.text(label))
        return layout

    def note(self, layout, message):
        label = QLabel(self.text(message))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    def table(self, layout, columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels([self.text(column) for column in columns])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        layout.addWidget(table)
        return table

    def fill(self, table, rows):
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                item = QTableWidgetItem(f"{value:.8g}" if isinstance(value, float) else str(value))
                table.setItem(i, j, item)

    def show_replay(self, rows):
        self.fill(self.backtest, rows)
        if rows:
            metrics = dict(count=len(rows), flat=terminal_mae(rows, 2),
                           price=terminal_mae(rows, 3), news=terminal_mae(rows, 4))
            message = self.text("{count} origins · Final-step MAE: flat {flat:.4g}, prices {price:.4g}, news {news:.4g}",
                                **metrics)
            if not self.scores:
                message += " · " + self.text("No news scores loaded.")
        else:
            message = self.text("Not enough history for this horizon.")
        self.backtest_status.setText(message)

    def run_local_replay(self):
        self.local_rows = []
        self.jobs = [("replay", origin, True, True) for origin in replay_origins(self.prices, self.horizon, 3)]
        self.next_job()

    def run_contribution(self):
        self.results = []
        self.jobs = [("inputs", len(self.prices), news, indicators) for news, indicators, _ in VARIANTS]
        self.next_job()

    def has_replay(self):
        return ready(self.model.root) and bool(replay_origins(self.prices, self.horizon))

    def run_paired_replay(self):
        self.paired_outcomes = {}
        self.fill(self.paired_table, [])
        self.jobs = [("paired", origin, news, indicators)
                     for origin in replay_origins(self.prices, self.horizon, 3)
                     for news, indicators, _ in VARIANTS]
        self.next_job()

    def show_paired_errors(self):
        variants = [(news, indicators) for news, indicators, _ in VARIANTS]
        count, flat, errors = paired_input_errors(self.paired_outcomes, variants)
        if not count:
            self.paired_status.setText(self.text("No origins had valid predictions for all input combinations."))
            return
        reference = errors[(False, False)]
        self.fill(self.paired_table, [
            (self.text(label), errors[(news, indicators)], errors[(news, indicators)] - reference)
            for news, indicators, label in VARIANTS
        ])
        message = self.text("{count} shared origins · Flat-price MAE {flat:.4g}.", count=count, flat=flat)
        if not self.scores:
            message += " " + self.text("No news scores loaded.")
        self.paired_status.setText(message)

    def next_job(self):
        if not self.jobs:
            self.local_button.setEnabled(self.has_replay())
            self.input_button.setEnabled(ready(self.model.root))
            self.paired_button.setEnabled(self.has_replay())
            return
        self.local_button.setEnabled(False)
        self.input_button.setEnabled(False)
        self.paired_button.setEnabled(False)
        self.job = mode, origin, news, indicators = self.jobs.pop(0)
        history = self.prices[:origin]
        facts = forecast_input(
            history, self.horizon, self.scores if news else {}, self.source,
            self.comparison_symbol if indicators else "", self.comparison if indicators else None,
        )
        if not indicators:
            facts.pop("market_context", None)
        target = self.local_status if mode == "replay" else self.input_note
        if mode == "paired":
            self.paired_status.setText(self.text("Running local model… {remaining} requests left.", remaining=len(self.jobs) + 1))
        else:
            target.setText(self.text("Running local model…"))
        self.facts = facts
        self.model.start_forecast(facts)

    def model_result(self, _task, response):
        if self.job is None:
            return
        mode, origin, news, indicators = self.job
        history = self.prices[:origin]
        try:
            value = forecast_path(response, history[-1].close, self.horizon,
                                  self.facts["research_evidence"]["bounded_adjustment_percent"],
                                  self.facts["historical_patterns"])[-1]
        except (ValueError, OverflowError):
            value = None
        unavailable = self.text("Unavailable")
        if mode == "replay":
            actual = self.prices[origin + self.horizon - 1].close
            self.local_rows.append((history[-1].day, actual, value if value is not None else unavailable,
                                    abs(actual - value) if value is not None else unavailable))
            self.fill(self.local_table, self.local_rows)
            if not self.jobs:
                errors = [row[3] for row in self.local_rows if isinstance(row[3], float)]
                if errors:
                    message = self.text("llama.cpp final-step MAE: {error:.4g} · {count} completed origins",
                                        error=sum(errors) / len(errors), count=len(errors))
                else:
                    message = self.text("No valid local forecasts were returned.")
                self.local_status.setText(message)
        elif mode == "paired":
            actual = self.prices[origin + self.horizon - 1].close
            _, _, predictions = self.paired_outcomes.setdefault(origin, (actual, history[-1].close, {}))
            if value is not None:
                predictions[(news, indicators)] = value
            if not self.jobs:
                self.show_paired_errors()
        else:
            label = ("Prices, news, and indicators" if news and indicators else
                     "Prices and news" if news else "Prices and indicators" if indicators else "Prices only")
            self.results.append((self.text(label), value))
            base = self.results[0][1]
            self.fill(self.input_table, [(name, value if value is not None else unavailable,
                                          value - base if base is not None and value is not None else unavailable)
                                         for name, value in self.results])
            if not self.jobs:
                note = self.text("A change in the estimate does not measure improvement.")
                if not self.scores:
                    note += " " + self.text("No news scores loaded.")
                self.input_note.setText(note)
        self.job = None
        QTimer.singleShot(0, self, self.next_job)

    def save_current(self):
        try:
            with closing(sqlite3.connect(self.database)) as connection:
                store = ForecastStore(connection)
                path = self.llama_path or self.result.forecast
                engine = "llama.cpp" if self.llama_path else "Statistical reference"
                store.save(self.symbol, self.prices[-1].day, engine, path)
            self.load_history()
        except (sqlite3.Error, ValueError):
            self.history_note.setText(self.text("Forecast journal unavailable."))

    def load_history(self):
        try:
            with closing(sqlite3.connect(self.database)) as connection:
                store = ForecastStore(connection)
                store.reconcile(self.symbol, self.prices)
                pending = self.text("Pending")
                self.saved_runs = store.runs(self.symbol)
                self.fill(self.history_table, [
                    (origin, self.text(engine), len(path), path[-1],
                     actual[-1][1] if len(actual) >= len(path) else pending,
                     abs(actual[-1][1] - path[-1]) if len(actual) >= len(path) else pending)
                    for origin, engine, path, actual in self.saved_runs
                ])
                if self.saved_runs:
                    self.history_table.setCurrentCell(0, 0)
                    self.show_saved_steps(0)
        except (sqlite3.Error, ValueError):
            self.history_note.setText(self.text("Forecast journal unavailable."))

    def show_saved_steps(self, row, *_):
        if not 0 <= row < len(self.saved_runs):
            self.fill(self.steps_table, [])
            return
        _, _, path, actual = self.saved_runs[row]
        pending = self.text("Pending")
        self.fill(self.steps_table, [
            (step + 1, value, actual[step][0], actual[step][1], abs(value - actual[step][1]))
            if step < len(actual) else (step + 1, value, pending, pending, pending)
            for step, value in enumerate(path)
        ])

    def done(self, result):
        self.jobs.clear()
        self.job = None
        self.model.stop_engine()
        self.worker.wait()
        super().done(result)
