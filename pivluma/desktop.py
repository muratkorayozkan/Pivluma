"""Offline market-analysis dashboard."""

import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, QStandardPaths, QStringListModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QCompleter, QFileDialog, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QStyle, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from pivluma import __version__
from pivluma.analyst import answer, price_statistics
from pivluma.appearance import THEMES, animations_enabled, colors_for, install_brand_font, stylesheet
from pivluma.chart import PriceChart
from pivluma.data import PriceStore, read_prices, sample_prices
from pivluma.dialogs import about_dialog, axis_dialog, history_dialog, settings_dialog, shortcuts_dialog
from pivluma.export import write_forecast
from pivluma.forecast import NEWS_MODEL, TRAINING_WINDOW, analyze
from pivluma.lab import ForecastStore
from pivluma.lab_dialog import ForecastLab
from pivluma.llama_forecast import FORECAST_TASK, forecast_input, forecast_path
from pivluma.local_assets import ready
from pivluma.local_model import LOCAL_MODEL, LocalModel
from pivluma.localization import translate
from pivluma.market import FX_SYMBOLS, OTHER_SYMBOLS, PUBLIC_SYMBOLS, MarketFeed
from pivluma.news import NewsFeed, align_scores, read_news_scores
from pivluma.patterns import historical_patterns
from pivluma.research import research_evidence, summarize_research
from pivluma.single_instance import SingleInstance
from pivluma.symbol_catalog import SymbolCatalog
from pivluma.update_ui import UpdateController


MAX_HORIZON = 120
DEFAULT_SYMBOL = "BTC/USD"
SHORTCUTS = (
    ("F11", "Toggle maximized window", "toggle_fullscreen"),
    ("Esc", "Restore window size", "exit_fullscreen"),
    ("Ctrl+K", "Open symbol list", "open_symbol_list"),
    ("Ctrl+Shift+F", "Chart focus", "toggle_chart_focus"),
    ("Ctrl+Shift+S", "Export forecast XLSX", "export_forecast"),
    ("Ctrl+,", "Settings", "open_settings"),
    ("F1", "Keyboard shortcuts", "open_shortcuts"),
)


class Dashboard(QMainWindow):
    update_status_changed = Signal()
    shortcuts = SHORTCUTS

    def __init__(self, database: Path):
        super().__init__()
        self.database = database
        self.language = "en"
        self.theme = "dark-blue"
        self.settings = None
        self.watchlist = []
        self.analyst_model = ""
        self.cloud_url = ""
        self.cloud_code = ""
        self.local_model = LocalModel(database.parent.resolve() / "local-ai-b12823", self)
        self.local_model.completed.connect(self.show_model_answer)
        self.llama_timer = QTimer(self, singleShot=True, interval=800)
        self.llama_timer.timeout.connect(self.request_llama_forecast)
        self.llama_pending = False
        self.llama_message = ""
        self.llama_source = ""
        self.llama_research = 0.
        self.llama_patterns = None
        self.localized = []
        self.market_message = "Twelve Data · Disconnected"
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("pivluma.ico"))))
        self.motion_enabled = animations_enabled()
        install_brand_font()
        for keys, _, method in SHORTCUTS:
            QShortcut(QKeySequence(keys), self, activated=getattr(self, method))
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
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(10)
        self.compact_title = self.brand()
        layout.addWidget(self.compact_title)
        header = QHBoxLayout()
        self.header_title = self.brand()
        header.addWidget(self.header_title)
        header.addStretch()
        self.language_label = self.localized_label("Language")
        header.addWidget(self.language_label)
        self.adjust_header()
        self.language_picker = QComboBox()
        self.language_picker.addItem("EN · English", "en")
        self.language_picker.addItem("TR · Türkçe", "tr")
        self.language_picker.addItem("ZH · 简体中文", "zh")
        self.language_picker.setAccessibleName(self.text("Language"))
        self.language_picker.currentIndexChanged.connect(self.change_language)
        header.addWidget(self.language_picker)
        self.update_button = self.localized_button("Check for updates")
        self.update_button.hide()
        for label, action in (("Revision history", history_dialog), ("About", about_dialog),
                              ("Settings", settings_dialog), ("Shortcuts", shortcuts_dialog)):
            button = self.localized_button(label)
            button.clicked.connect(lambda checked=False, callback=action: callback(self))
            button.setProperty("headerAction", True)
            header.addWidget(button)
        self.update_button.setProperty("headerAction", True)
        header.addWidget(self.update_button)
        self.update_message = ""
        self.update_icon = QLabel()
        self.update_icon.setFixedWidth(18)
        self.update_icon.hide()
        header.addWidget(self.update_icon)
        self.update_label = QLabel()
        self.update_label.setObjectName("updateStatus")
        self.update_label.hide()
        header.addWidget(self.update_label)
        layout.addLayout(header)
        market_section = QVBoxLayout()
        market_heading = self.localized_label("Market data")
        market_heading.setProperty("sectionTitle", True)
        category_row = QHBoxLayout()
        category_row.addWidget(market_heading)
        category_row.addStretch()
        market_section.addLayout(category_row)
        layout.addLayout(market_section)
        toolbar = QHBoxLayout()
        market_section.addLayout(toolbar)
        self.symbol_picker = QComboBox()
        self.symbol_picker.setEditable(True)
        self.symbol_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.symbol_picker.setMinimumContentsLength(12)
        self.symbol_picker.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.symbol = self.symbol_picker.lineEdit()
        self.symbol_groups = (
            ("Cryptocurrency", PUBLIC_SYMBOLS),
            ("Foreign exchange", FX_SYMBOLS),
            ("Stocks", {code: title for code, title in OTHER_SYMBOLS.items() if code not in ("SPY", "QQQ", "DIA")}),
            ("ETFs", {code: OTHER_SYMBOLS[code] for code in ("SPY", "QQQ", "DIA")}),
        )
        self.symbol_category = QComboBox()
        for name in ("All symbols", *(name for name, _ in self.symbol_groups)):
            index = self.symbol_category.count()
            self.symbol_category.addItem(self.text(name))
            self.localize(lambda text, index=index: self.symbol_category.setItemText(index, text), name)
        self.localize(self.symbol_category.setAccessibleName, "Symbol category")
        self.stock_catalog = SymbolCatalog(self)
        self.stock_catalog.loaded.connect(self.update_catalog)
        self.completion_model = QStringListModel(self)
        self.symbol_completer = QCompleter(self.completion_model, self)
        self.symbol_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.symbol_picker.setCompleter(self.symbol_completer)
        self.symbol_completer.activated[str].connect(self.open_watch_symbol)
        self.symbol_category.currentIndexChanged.connect(self.populate_symbols)
        category_row.addWidget(self.symbol_category)
        self.populate_symbols()
        self.symbol_picker.activated.connect(self.select_live_symbol)
        self.symbol.returnPressed.connect(self.connect_market)
        self.localize(self.symbol.setPlaceholderText, "Symbol")
        self.localize(self.symbol.setAccessibleName, "Symbol")
        toolbar.addWidget(self.symbol_picker, 1)
        watch_button = self.localized_button("Watchlist")
        self.watch_menu = QMenu(watch_button)
        self.watch_menu.aboutToShow.connect(self.populate_watchlist)
        watch_button.setMenu(self.watch_menu)
        toolbar.addWidget(watch_button)
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
        lab = self.localized_button("Forecast Lab")
        lab.clicked.connect(self.open_forecast_lab)
        forecast_controls.addWidget(lab)
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
        self.axis_button = self.localized_button("Price axis")
        self.axis_button.clicked.connect(lambda: axis_dialog(self))
        chart_controls.addWidget(self.axis_button)
        reset = self.localized_button("Reset chart")
        reset.clicked.connect(self.reset_chart)
        chart_controls.addWidget(reset)
        chart_controls.addStretch()
        compare = self.localized_button("Compare symbol")
        compare.clicked.connect(self.choose_comparison)
        chart_controls.addWidget(compare)
        chart_layout.addLayout(chart_controls)
        compare_row = QHBoxLayout()
        self.comparison_status = QLabel()
        self.comparison_status.setTextFormat(Qt.TextFormat.PlainText)
        self.comparison_status.setWordWrap(True)
        compare_row.addWidget(self.comparison_status, 1)
        self.clear_compare = self.localized_button("Clear comparison")
        self.clear_compare.clicked.connect(self.clear_comparison)
        compare_row.addWidget(self.clear_compare)
        self.clear_compare.hide()
        chart_layout.addLayout(compare_row)
        self.comparison_market = MarketFeed(self)
        self.comparison_market.history.connect(self.receive_comparison)
        self.comparison_market.history_warning.connect(self.comparison_error)
        self.comparison_market.status.connect(self.comparison_state)
        self.pending_comparison = ""
        self.comparison_failed = False
        self.llama_button = self.localized_button("Run llama.cpp forecast")
        self.llama_button.clicked.connect(self.request_llama_forecast)
        self.llama_status = QLabel()
        self.llama_status.setWordWrap(True)
        chart_layout.addWidget(self.llama_button, alignment=Qt.AlignmentFlag.AlignLeft)
        chart_layout.addWidget(self.llama_status)
        self.chart = PriceChart(self.motion_enabled)
        self.history_range.currentIndexChanged.connect(
            lambda: self.chart.set_history_limit(self.history_range.currentData())
        )
        layers = QHBoxLayout()
        for key, title in (("close", "Close"), ("average", "SMA 20"),
                           ("llama", "llama.cpp"),
                           ("llama_band", "llama band")):
            control = QCheckBox()
            self.localize(control.setText, title)
            control.setChecked(self.chart.layers[key])
            control.toggled.connect(lambda enabled, key=key: self.chart.set_layer(key, enabled))
            layers.addWidget(control)
            if key == "llama":
                self.llama_layer = control
                self.llama_layer.hide()
            elif key == "llama_band":
                self.llama_band_layer = control
                self.llama_band_layer.hide()
        layers.addStretch()
        chart_layout.addLayout(layers)
        chart_layout.addWidget(self.chart, 1)
        self.point_details = QLabel()
        self.point_details.setWordWrap(True)
        self.point_details.setTextFormat(Qt.TextFormat.PlainText)
        chart_layout.addWidget(self.point_details)
        self.chart.selected.connect(self.show_chart_point)
        self.chart_legend = self.localized_label(
            "Closing price  /  Dash-dot llama forecast  /  Dotted scenario band"
        )
        self.chart_legend.setWordWrap(True)
        chart_layout.addWidget(self.chart_legend)
        self.metrics = QLabel()
        self.metrics.setWordWrap(True)
        self.metrics.setObjectName("metrics")
        chart_layout.addWidget(self.metrics)
        self.export_button = self.localized_button("Export forecast XLSX")
        self.export_button.clicked.connect(self.export_forecast)
        forecast_controls.addWidget(self.export_button)
        self.chart_focus_button = self.localized_button("Chart focus")
        self.chart_focus_button.setCheckable(True)
        self.chart_focus_button.toggled.connect(lambda _: self.arrange_workspace())
        forecast_controls.addWidget(self.chart_focus_button)
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
            ("Volatility", "What is the volatility?"),
            ("Max drawdown", "What was the maximum drawdown?"),
            ("Analyze news", "Analyze recent news scores"),
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
        self.chart.viewport_changed.connect(self.render_comparison)
        self.chart.selected.connect(lambda _: self.refresh_contextual_reply())
        self.arrange_workspace()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        market_controls = QHBoxLayout()
        self.market_status = QLabel("Twelve Data · Disconnected")
        self.market_status.setWordWrap(True)
        market_controls.addWidget(self.market_status, 1)
        self.market_progress = QProgressBar()
        self.market_progress.setRange(0, 0)
        self.market_progress.setTextVisible(False)
        self.market_progress.setFixedSize(84, 5)
        self.market_progress.hide()
        market_controls.addWidget(self.market_progress)
        market_section.addLayout(market_controls)
        self.quote_label = QLabel()
        self.quote_label.setTextFormat(Qt.TextFormat.PlainText)
        self.quote_label.setWordWrap(True)
        self.quote_label.setObjectName("quote")
        market_section.addWidget(self.quote_label)
        self.provider_credit = self.localized_button("Data provided by Twelve Data")
        self.provider_credit.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://twelvedata.com/")))
        self.provider_credit.hide()
        market_section.addWidget(self.provider_credit, alignment=Qt.AlignmentFlag.AlignLeft)
        self.history_warning = QLabel()
        self.history_warning.setWordWrap(True)
        self.history_warning.hide()
        market_section.addWidget(self.history_warning)
        self.history_message = ""
        self.news_scores = {}
        self.news_source = ""
        self.news_message = ""
        self.news = NewsFeed(self, self.database.with_name("news-cache.json") if self.database.is_absolute() else None)
        self.news.status.connect(self.set_news_status)
        self.news.loaded.connect(self.receive_news)
        news_controls = QHBoxLayout()
        fetch_news = self.localized_button("Research news")
        fetch_news.clicked.connect(self.fetch_news)
        news_controls.addWidget(fetch_news)
        import_news = self.localized_button("Import news scores")
        import_news.clicked.connect(self.import_news)
        news_controls.addWidget(import_news)
        self.news_status = QLabel()
        self.news_status.setWordWrap(True)
        news_controls.addWidget(self.news_status, 1)
        market_section.addLayout(news_controls)
        self.last_quote = None
        self.stream_lost = True
        self.live_symbol = ""
        self.series_source = ""
        self.market = MarketFeed(self)
        self.market.history.connect(self.receive_history)
        self.market.history_warning.connect(self.receive_market_warning)
        self.market.status.connect(self.set_market_status)
        self.market.quote.connect(self.receive_quote)
        self.market.stream_lost.connect(self.mark_stream_lost)
        self.quote_timer = QTimer(self, interval=5000)
        self.quote_timer.timeout.connect(self.update_quote)
        self.quote_timer.start()
        self.show_sample()
        self.change_theme(self.theme)

    def toggle_fullscreen(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def exit_fullscreen(self):
        if self.isMaximized():
            self.showNormal()

    def brand(self):
        group = QWidget()
        group.setObjectName("brand")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        title = QLabel("Pivluma")
        title.setObjectName("title")
        layout.addWidget(title)
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(29, 29))
        layout.addWidget(logo, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch()
        return group

    def populate_symbols(self):
        current = self.symbol.text()
        section = self.symbol_category.currentIndex()
        groups = self.symbol_groups if not section else self.symbol_groups[section - 1:section]
        self.symbol_picker.clear()
        for category, instruments in groups:
            if not section:
                self.symbol_picker.addItem(self.text(category))
                heading = self.symbol_picker.model().item(self.symbol_picker.count() - 1)
                bold = QFont(heading.font())
                bold.setBold(True)
                heading.setFont(bold)
                heading.setBackground(QColor(self.chart.colors[3] if hasattr(self, "chart") else "#40526c"))
                heading.setEnabled(False)
            for code, name in instruments.items():
                self.symbol_picker.addItem(f"{self.text(name)} ({code})", code)
        self.symbol_picker.setCurrentIndex(-1)
        self.symbol.setText(current)
        self.completion_model.setStringList(sorted({code for _, instruments in groups for code in instruments}))
        if section and groups[0][0] in ("Stocks", "ETFs"):
            self.stock_catalog.load(groups[0][0])

    def update_catalog(self, category, symbols):
        section = self.symbol_category.currentIndex()
        if section and self.symbol_groups[section - 1][0] == category:
            combined = set(self.completion_model.stringList()) | set(symbols)
            self.completion_model.setStringList(sorted(combined))

    def open_forecast_lab(self):
        if not self.prices or self.chart.result is None:
            self.status.setText(self.text("Price history is needed for analysis."))
            return
        self.cancel_model_request()
        self.local_model.stop_engine()
        dialog = ForecastLab(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def activate_existing(self):
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def open_symbol_list(self):
        self.symbol.setFocus()
        self.symbol_picker.showPopup()

    def toggle_chart_focus(self):
        self.chart_focus_button.toggle()

    def populate_watchlist(self):
        self.watch_menu.clear()
        symbol = self.symbol.text().strip().upper()
        saved = symbol in self.watchlist
        label = "Remove {symbol}" if saved else "Add {symbol} to watchlist"
        action = self.watch_menu.addAction(
            self.text(label, symbol=symbol) if symbol else self.text("Select a symbol first")
        )
        action.setEnabled(bool(re.fullmatch(r"[A-Z0-9][A-Z0-9./:_-]{0,31}", symbol)))
        action.triggered.connect(lambda: self.toggle_watch_symbol(symbol))
        self.watch_menu.addSeparator()
        if not self.watchlist:
            self.watch_menu.addAction(self.text("No saved symbols")).setEnabled(False)
        for entry in self.watchlist:
            self.watch_menu.addAction(entry, lambda entry=entry: self.open_watch_symbol(entry))

    def toggle_watch_symbol(self, symbol):
        if symbol in self.watchlist:
            self.watchlist.remove(symbol)
        else:
            self.watchlist.append(symbol)
        if self.settings is not None:
            self.settings.setValue("watchlist", self.watchlist)

    def open_watch_symbol(self, symbol):
        self.symbol.setText(symbol)
        self.connect_market()

    def open_settings(self):
        settings_dialog(self)

    def open_shortcuts(self):
        shortcuts_dialog(self)

    def start_default_market(self):
        self.select_live_symbol(self.symbol_picker.findData(DEFAULT_SYMBOL))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "workspace_stack"):
            self.arrange_workspace()
            self.adjust_header()
            self.update_label.setVisible(bool(self.update_message) and self.width() >= 1120)

    def adjust_header(self):
        inline = self.width() >= 1120
        self.header_title.setVisible(inline)
        self.compact_title.setVisible(not inline)
        self.language_label.setVisible(inline)

    def render_update_status(self):
        message = self.update_message
        states = {
            "Checking and preparing updates…": ("Checking", QStyle.StandardPixmap.SP_BrowserReload),
            "No newer verified update found.": ("Up to date", QStyle.StandardPixmap.SP_DialogApplyButton),
            "Update ready.": ("Available", QStyle.StandardPixmap.SP_ArrowDown),
        }
        short, icon = states.get(message, ("Check failed", QStyle.StandardPixmap.SP_MessageBoxWarning))
        self.update_icon.setPixmap(self.style().standardIcon(icon).pixmap(16, 16))
        self.update_icon.setToolTip(self.text(message))
        self.update_icon.setAccessibleName(self.text(message))
        self.update_label.setText(self.text(short) if message else "")
        self.update_label.setToolTip(self.text(message))
        self.update_icon.setVisible(bool(message) and not self.update_button.isHidden())
        self.update_label.setVisible(bool(message) and self.width() >= 1120 and not self.update_button.isHidden())
        self.update_status_changed.emit()

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
        focused = self.chart_focus_button.isChecked()
        if wide:
            self.analyst_panel.setVisible(not focused)
        else:
            self.workspace_tabs.setTabVisible(1, not focused)
            if focused:
                self.workspace_tabs.setCurrentIndex(0)
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
        self.populate_symbols()
        self.render_update_status()
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
        self.populate_symbols()
        if self.workspace_tabs.count() == 2:
            self.workspace_tabs.setTabText(0, self.text("Chart"))
            self.workspace_tabs.setTabText(1, self.text("Local analyst"))
        self.language_picker.setAccessibleName(self.text("Language"))
        self.chart.language = self.language
        self.chart.setAccessibleName(self.text("Closing prices and forecast interval"))
        self.chart.update()
        self.set_market_status(self.market_message)
        self.set_history_warning(self.history_message)
        self.update_news_status()
        self.update_quote()
        self.update_summary()
        self.show_chart_point(self.chart.selected_index)
        self.update_llama_status()
        self.render_comparison()
        self.render_update_status()
        self.cancel_model_request()
        if self.reply.text():
            self.render_analyst(False)
        self.status.clear()

    def set_market_status(self, message):
        self.market_message = message
        self.market_status.setText(self.text(message))
        busy = message.endswith(("Loading daily history", "Connecting quote stream", "Awaiting subscription"))
        self.market_progress.setVisible(self.motion_enabled and busy)

    def receive_market_warning(self, message):
        self.set_history_warning(message)
        if not self.prices and self.load_equity_history(self.live_symbol):
            self.set_market_status("Showing saved closes; live equity data is unavailable.")

    def set_history_warning(self, message):
        self.history_message = message
        self.history_warning.setText(self.text(message))
        self.history_warning.setVisible(bool(message))

    def set_news_status(self, message):
        self.news_message = message
        self.update_news_status()

    def update_news_status(self):
        if self.news_message:
            message = self.text(self.news_message)
        elif self.news_scores and self.chart.result:
            result = self.chart.result
            mode = self.text("Research model selected" if result.model == NEWS_MODEL else "Price model selected")
            message = self.text("{source} · {count} matched research/return pairs · {mode}",
                                source=self.text(self.news_source), count=result.news_pairs, mode=mode)
        else:
            message = ""
        self.news_status.setText(message)

    def reset_news(self):
        self.news.cancel()
        self.news_scores = {}
        self.news_source = ""
        self.set_news_status("")

    def fetch_news(self):
        if not self.prices or not self.series_symbol:
            self.set_news_status("Open a price series with a symbol before researching news.")
            return
        self.news.start(self.series_symbol)

    def receive_news(self, symbol, scores, source="GDELT news tone"):
        if self.prices and symbol == self.series_symbol:
            self.news_scores = scores
            self.news_source = source
            self.news_message = ""
            self.refresh()

    def import_news(self):
        if not self.prices:
            self.set_news_status("Open a price series before importing news scores.")
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, self.text("Import news scores"), "", self.text("CSV files (*.csv)")
        )
        if not filename:
            return
        try:
            with Path(filename).open(encoding="utf-8-sig", newline="") as stream:
                scores = read_news_scores(stream)
        except (OSError, TypeError, ValueError) as error:
            self.set_news_status("News CSV could not be read. Use date and tone columns.")
            return
        self.news.cancel()
        self.news_scores = scores
        self.news_source = "Imported news scores"
        self.news_message = ""
        self.refresh()

    def select_live_symbol(self, index):
        symbol = self.symbol_picker.itemData(index)
        if symbol:
            self.symbol.setText(symbol)
            self.connect_market()

    def clear_analysis(self, symbol):
        self.cancel_model_request()
        self.reset_news()
        self.series_source = ""
        self.update_provider_credit()
        self.series_symbol = symbol
        self.prices = []
        self.chart.history = []
        self.chart.days = []
        self.chart.result = None
        self.clear_llama_forecast()
        self.chart.plot_area.setWidth(0)
        self.chart.view_changed()
        self.horizon.setEnabled(False)
        self.export_button.setEnabled(False)
        self.axis_button.setEnabled(False)
        self.reply.clear()
        self.update_summary()

    def connect_market(self):
        try:
            symbol = self.symbol.text().strip().upper()
            if not symbol:
                self.symbol_picker.showPopup()
                return
            if ',' in symbol:
                raise ValueError(self.text("Enter one symbol for live data, such as AAPL or EUR/USD."))
            if symbol == self.live_symbol and (self.market.pending is not None or self.market.socket is not None):
                return
            self.market.stop()
            self.last_quote = None
            self.live_symbol = symbol
            self.set_history_warning("")
            self.update_quote()
            self.status.clear()
            self.clear_analysis(symbol)
            key = None
            if symbol not in PUBLIC_SYMBOLS:
                key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
                if not key or not key.isascii() or not key.isprintable() or " " in key:
                    if symbol not in FX_SYMBOLS:
                        if self.load_equity_history(symbol):
                            self.set_market_status("Showing saved closes; live equity data is unavailable.")
                        else:
                            self.set_market_status("Equity data needs a configured feed or imported CSV history.")
                        return
                    key = None
            self.market.start(symbol, key)
        except ValueError as error:
            self.status.setText(str(error))

    def load_equity_history(self, symbol):
        if not symbol or symbol in PUBLIC_SYMBOLS or symbol in FX_SYMBOLS or not self.database.is_file():
            return False
        try:
            with closing(sqlite3.connect(self.database)) as connection:
                prices = PriceStore(connection).load(symbol)
            if len(prices) <= TRAINING_WINDOW + 9:
                return False
            self.set_series("Saved equity closes", prices, symbol)
        except (sqlite3.Error, ValueError):
            return False
        return True

    def choose_comparison(self):
        try:
            with closing(sqlite3.connect(self.database)) as connection:
                saved = PriceStore(connection).symbols()
        except sqlite3.Error:
            saved = []
        symbols = sorted((set(saved) | PUBLIC_SYMBOLS.keys() | FX_SYMBOLS.keys()) - {self.series_symbol})
        if not symbols:
            return
        symbol, accepted = QInputDialog.getItem(
            self, self.text("Compare symbol"), self.text("Symbol"), symbols, 0, False
        )
        if not accepted:
            return
        self.clear_comparison()
        if symbol in saved:
            try:
                with closing(sqlite3.connect(self.database)) as connection:
                    prices = PriceStore(connection).load(symbol)
                if prices:
                    self.receive_comparison(symbol, prices)
                    return
            except (sqlite3.Error, ValueError):
                self.comparison_error()
                return
        if symbol not in PUBLIC_SYMBOLS and symbol not in FX_SYMBOLS:
            self.comparison_error()
            return
        self.pending_comparison = symbol
        self.render_comparison()
        self.comparison_market.start(symbol, None, history_only=True)

    def comparison_error(self, *_):
        self.pending_comparison = ""
        self.comparison_failed = True
        self.render_comparison()

    def comparison_state(self, message):
        if "unavailable" in message or "rejected" in message:
            self.comparison_error()

    def receive_comparison(self, symbol, prices):
        self.pending_comparison = ""
        self.comparison_failed = False
        self.chart.comparison_symbol = symbol
        self.chart.comparison_closes = {price.day: price.close for price in prices}
        self.chart.view_changed()
        if self.analyst_model == LOCAL_MODEL and self.prices:
            self.llama_timer.start()

    def clear_comparison(self):
        had_comparison = bool(self.chart.comparison_symbol)
        self.comparison_market.stop()
        self.pending_comparison = ""
        self.comparison_failed = False
        self.chart.comparison_symbol = ""
        self.chart.comparison_closes = {}
        self.chart.view_changed()
        if had_comparison and self.analyst_model == LOCAL_MODEL and self.prices:
            self.llama_timer.start()

    def render_comparison(self):
        symbol = self.chart.comparison_symbol
        visible = bool(self.pending_comparison or symbol or self.comparison_failed)
        self.clear_compare.setVisible(visible)
        self.comparison_status.setVisible(visible)
        if self.pending_comparison:
            text = self.text("Loading comparison for {symbol}…", symbol=self.pending_comparison)
        elif self.comparison_failed:
            text = self.text("Comparison price history unavailable.")
        elif symbol and self.chart.comparison_points():
            text = self.text("Dashed purple line · {symbol} · fixed reference {date}.",
                             symbol=symbol, date=self.chart.comparison_anchor()[0])
        elif symbol:
            text = self.text("{symbol} has fewer than two common dates in this view.", symbol=symbol)
        else:
            text = ""
        self.comparison_status.setText(text)

    def stop_market(self):
        self.market.stop()
        self.set_market_status(self.market.with_provider("Twelve Data · Disconnected"))
        self.mark_stream_lost()

    def receive_history(self, symbol, prices):
        if symbol != self.live_symbol:
            return
        if len(prices) <= TRAINING_WINDOW + 9:
            self.receive_market_warning("Too few daily observations for a forecast.")
            return
        source = ("ECB daily reference rates" if self.market.provider == "ECB"
                  else self.market.with_provider("Twelve Data daily history"))
        self.set_series(source, prices, symbol)
        self.fetch_news()

    def receive_quote(self, price, timestamp):
        if self.last_quote is None or timestamp >= self.last_quote[1]:
            self.stream_lost = False
            self.last_quote = price, timestamp
            self.update_quote()

    def update_provider_credit(self):
        historical = self.series_source == "Twelve Data daily history"
        streaming = self.last_quote is not None and self.market.provider == "Twelve Data"
        self.provider_credit.setVisible(historical or streaming)

    def update_quote(self):
        self.update_provider_credit()
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
        self.reset_news()
        self.series_source = label
        self.update_provider_credit()
        self.series_symbol = symbol
        if symbol and (symbol == self.chart.comparison_symbol or symbol == self.pending_comparison):
            self.clear_comparison()
        self.prices = prices
        self.horizon.setEnabled(True)
        self.export_button.setEnabled(True)
        self.axis_button.setEnabled(True)
        self.chart.set_axis()
        self.chart.history_offset = 0
        self.horizon.blockSignals(True)
        self.horizon.setMaximum(min(MAX_HORIZON, len(prices) - TRAINING_WINDOW - 9))
        self.horizon.blockSignals(False)
        self.refresh()
        self.chart.reveal(history=True)

    def ask_topic(self, prompt):
        self.question.setText(self.text(prompt))
        self.ask_analyst()

    def contextual_topic(self):
        question = self.question.text().casefold()
        if any(term in question for term in ("selected point", "seçili nokta", "seçilen nokta", "选中的点")):
            return "point"
        if any(term in question for term in ("visible period", "görünür dönem", "grafikteki dönem", "可见区间")):
            return "period"
        return None

    def refresh_contextual_reply(self):
        if self.reply.text() and self.contextual_topic() is not None:
            self.cancel_model_request()
            self.render_analyst(False)

    def set_cloud_access(self, address, code):
        self.cancel_model_request()
        self.cloud_url = address.strip()
        self.cloud_code = code.strip()
        if self.settings is not None:
            self.settings.setValue("cloud_url", self.cloud_url)

    def restore_analyst_model(self):
        saved = self.settings.value("analyst_model", "", type=str)
        if ready(self.local_model.root) and not self.settings.value("local_model_default_applied", False, type=bool):
            saved = LOCAL_MODEL
        self.set_analyst_model(saved)

    def set_analyst_model(self, model):
        self.cancel_model_request()
        self.local_model.stop_engine()
        if model not in ("builtin", "cloud", LOCAL_MODEL):
            model = LOCAL_MODEL if ready(self.local_model.root) else ""
        self.analyst_model = "" if model == "builtin" or (model == LOCAL_MODEL and not ready(self.local_model.root)) else model
        if self.settings is not None:
            self.settings.setValue("analyst_model", model)
            if self.analyst_model == LOCAL_MODEL:
                self.settings.setValue("local_model_default_applied", True)
        self.update_llama_status()
        if self.analyst_model == LOCAL_MODEL and self.prices and not self.chart.llama_forecast:
            self.llama_timer.start()

    def ask_analyst(self):
        self.cancel_model_request()
        self.render_analyst(True)

    def render_analyst(self, use_model):
        if not self.prices:
            self.reply.setText(self.text("Price history is needed for analysis."))
            return
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
        if self.is_research_question(question):
            evidence = research_evidence(prices, self.news_scores, self.text(self.news_source))
            patterns = historical_patterns(prices, self.news_scores, self.horizon.value())
            message = f"{self.series_label}\n{summarize_research(evidence, self.language, patterns)}"
        else:
            message = answer(question, self.series_label, prices, self.chart.result, self.language)
        self.reply.setText(message)
        self.request_model_answer(use_model, message)

    @staticmethod
    def is_research_question(question):
        return any(word in question.casefold() for word in ("news", "haber", "新闻", "情绪", "sentiment", "headlines"))

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
            "news_source": self.news_source, "matched_news_return_pairs": result.news_pairs,
        }
        self.model_calculation = calculation
        self.reply.setText(calculation + "\n\n" + self.text("Local model is preparing an explanation…"))
        if self.analyst_model == "cloud":
            self.local_model.start_cloud(self.cloud_url, self.cloud_code, self.language, question[:2000], facts)
        else:
            facts["price_statistics"] = price_statistics(self.prices)
            facts["research_evidence"] = research_evidence(self.prices, self.news_scores, self.news_source)
            facts["historical_patterns"] = historical_patterns(self.prices, self.news_scores, self.horizon.value())
            if self.is_research_question(question):
                facts = {key: facts[key] for key in ("series", "last_date", "research_evidence", "historical_patterns")}
                facts["analysis_topic"] = "research"
            self.local_model.start(self.analyst_model, self.language, question[:2000], facts)

    def show_model_answer(self, model, response):
        if model == FORECAST_TASK:
            self.receive_llama_forecast(response)
            return
        if model == "cloud":
            explanation = (
                self.text("Cloud analyst") + "\n" + response
                if response else self.text(self.local_model.cloud_error)
            )
        else:
            explanation = (
                self.text("Local model ({model})", model="Qwen3 1.7B") + "\n" + response
                if response else self.text("Local model unavailable. Check the llama.cpp download in Settings.")
            )
        self.reply.setText(self.model_calculation + "\n\n" + explanation)

    def closeEvent(self, event):
        self.comparison_market.stop()
        self.local_model.stop_engine()
        super().closeEvent(event)

    def export_forecast(self):
        result = self.chart.result
        if result is None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, self.text("Export forecast"), "forecast.xlsx", self.text("Excel workbook (*.xlsx)")
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".xlsx":
            path = path.with_suffix(".xlsx")
        try:
            write_forecast(path, result, self.prices[-1].day, self.series_label, self.text)
        except OSError as error:
            self.status.setText(self.text("Could not export forecast: {error}", error=error))
            return
        self.status.setText(self.text("Forecast exported to {path}", path=path))

    def cancel_model_request(self):
        self.llama_timer.stop()
        self.local_model.cancel()
        if self.llama_pending:
            self.llama_pending = False
            self.llama_message = "llama.cpp forecast interrupted."
            self.update_llama_status()

    def clear_llama_forecast(self):
        self.chart.llama_forecast = ()
        self.chart.hover_index = None
        self.chart.selected_index = None
        self.llama_message = ""
        self.llama_source = ""
        self.llama_research = 0.
        self.llama_patterns = None
        self.update_llama_status()

    def update_llama_status(self):
        installed = ready(self.local_model.root)
        self.llama_button.setVisible(installed)
        self.llama_layer.setVisible(bool(self.chart.llama_forecast))
        self.llama_band_layer.setVisible(bool(self.chart.llama_forecast))
        self.llama_button.setEnabled(bool(self.prices) and not self.llama_pending)
        self.llama_status.setVisible(bool(self.llama_message))
        source = self.text(self.llama_source or "Price history only")
        self.llama_status.setText(self.text(self.llama_message, source=source))

    def request_llama_forecast(self):
        self.llama_timer.stop()
        if not self.prices or not ready(self.local_model.root):
            return
        if self.local_model.reply is not None and hasattr(self, "model_calculation") and not self.llama_pending:
            self.reply.setText(self.model_calculation)
        self.llama_pending = True
        facts = forecast_input(
            self.prices, self.horizon.value(), self.news_scores, self.news_source,
            self.chart.comparison_symbol, self.chart.comparison_closes,
        )
        self.llama_source = facts["research_source"]
        self.llama_research = facts["research_evidence"]["bounded_adjustment_percent"]
        self.llama_patterns = facts["historical_patterns"]
        self.llama_message = "Generating llama.cpp forecast · {source}"
        self.update_llama_status()
        self.local_model.start_forecast(facts)

    def receive_llama_forecast(self, response):
        if not self.llama_pending:
            return
        self.llama_pending = False
        try:
            self.chart.llama_forecast = forecast_path(
                response, self.prices[-1].close, self.horizon.value(), self.llama_research, self.llama_patterns
            )
        except (OverflowError, ValueError):
            self.llama_message = "llama.cpp forecast unavailable."
        else:
            self.llama_message = "Dash-dot line · llama.cpp forecast · {source}. Dotted limits reuse the statistical error widths."
            if self.series_symbol and self.database.is_absolute():
                try:
                    with closing(sqlite3.connect(self.database)) as connection:
                        store = ForecastStore(connection)
                        store.save(self.series_symbol, self.prices[-1].day, "llama.cpp", self.chart.llama_forecast)
                        store.reconcile(self.series_symbol, self.prices)
                except (sqlite3.Error, ValueError):
                    self.status.setText(self.text("Forecast journal unavailable."))
            self.chart.reveal()
            self.show_chart_point(self.chart.selected_index)
        self.update_llama_status()

    def refresh(self):
        if not self.prices:
            return
        self.cancel_model_request()
        closes = [price.close for price in self.prices]
        news = align_scores(self.prices, self.news_scores) if self.news_scores else None
        result = analyze(closes, self.horizon.value(), news)
        self.chart.history = closes
        self.chart.days = [price.day for price in self.prices]
        self.chart.result = result
        self.clear_llama_forecast()
        if self.analyst_model == LOCAL_MODEL:
            self.llama_timer.start()
        self.update_news_status()
        self.chart.hover_index = None
        self.chart.select_index(None)
        self.update_summary()
        self.render_comparison()
        self.reply.clear()
        self.status.clear()

    def update_summary(self):
        if not self.prices:
            self.dataset.setText(self.text("{symbol} · No price history loaded", symbol=self.series_symbol))
            self.metrics.clear()
            return
        source = self.text(self.series_source)
        self.series_label = f"{self.series_symbol} · {source}" if self.series_symbol else source
        self.dataset.setText(self.text(
            "{label}  ·  {start} to {end}  ·  {count} observations",
            label=self.series_label, start=self.prices[0].day, end=self.prices[-1].day,
            count=len(self.prices),
        ))
        result = self.chart.result
        last_close = self.prices[-1].close
        change = (last_close / self.prices[-2].close - 1) * 100
        fields = (
            ("Selected model", self.text(result.model)),
            ("Last close", f"{last_close:.8g}"),
            ("Close change", f"{change:+.2f}%"),
            ("Final forecast", f"{result.forecast[-1]:.8g}"),
            ("Model MAE", f"{result.mae:.2f}"),
            ("Last-price MAE", f"{result.baseline_mae:.2f}"),
            ("Test origins", str(result.origins)),
        )
        reference = self.text("Statistical reference")
        self.metrics.setText(f"{reference}\n" + "     |     ".join(
            f"{self.text(label)}  {value}" for label, value in fields
        ))


def main():
    application = QApplication(sys.argv)
    application.setApplicationName("Pivluma")
    application.setApplicationVersion(__version__)
    application.setStyle("Fusion")
    directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    directory.mkdir(parents=True, exist_ok=True)
    instance = SingleInstance(directory)
    if not instance.acquire(replacing="--replace-instance" in sys.argv[1:]):
        return 0
    application.aboutToQuit.connect(instance.release)
    window = Dashboard(directory / "pivluma.sqlite3")
    instance.activate_on_connection(window.activate_existing)
    application.setWindowIcon(window.windowIcon())
    settings = QSettings("Pivluma", "Pivluma")
    window.settings = settings
    window.watchlist = settings.value("watchlist", [], type=list)
    window.change_theme(settings.value("theme", "dark-blue", type=str))
    window.set_cloud_access(settings.value("cloud_url", "", type=str), "")
    window.restore_analyst_model()
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
            started, _ = QProcess.startDetached(str(preferred), ["--replace-instance"], str(preferred.parent))
            if started:
                instance.release()
                return 0
        window.update_button.show()
        UpdateController(window, window.update_button, updates, settings)
    window.showMaximized()
    QTimer.singleShot(0, window, window.start_default_market)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
