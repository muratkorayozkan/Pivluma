"""Desktop dialogs for appearance, chart limits, and published release notes."""

import json
import platform
import sqlite3
import sys
from pathlib import Path
from contextlib import closing

from PySide6.QtCore import QByteArray, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QHeaderView, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from pivluma import __version__
from pivluma.appearance import THEMES
from pivluma.lab import ForecastStore
from pivluma.local_assets import ready, supported
from pivluma.local_model import LOCAL_MODEL
from pivluma.model_download import DownloadDialog
from pivluma.tradingview import tradingview_url
from pivluma.updates import ROOT


def _dialog(window, title):
    dialog = QDialog(window)
    dialog.setWindowTitle(window.text(title))
    return dialog


def _buttons(dialog, layout, kind):
    buttons = QDialogButtonBox(kind)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return buttons


def settings_dialog(window):
    dialog = _dialog(window, "Settings")
    form = QFormLayout(dialog)
    themes = QComboBox()
    for key, label in THEMES.items():
        themes.addItem(window.text(label), key)
    themes.setCurrentIndex(max(0, themes.findData(window.theme)))
    form.addRow(window.text("Theme"), themes)
    model = QComboBox()
    model.addItem(window.text("Built-in analyst"), "builtin")
    model.addItem(window.text("Cloud analyst"), "cloud")
    model.addItem("llama.cpp · Qwen3 1.7B", LOCAL_MODEL)
    model.setCurrentIndex(max(0, model.findData(window.analyst_model)))
    form.addRow(window.text("Analyst model"), model)
    cloud_url = QLineEdit(window.cloud_url)
    cloud_code = QLineEdit(window.cloud_code)
    cloud_code.setEchoMode(QLineEdit.EchoMode.Password)
    form.addRow(window.text("Cloud server URL"), cloud_url)
    form.addRow(window.text("Cloud access code"), cloud_code)
    cloud_note = QLabel(window.text("Cloud mode sends your question and calculated facts to the configured server."))
    cloud_note.setWordWrap(True)
    form.addRow(cloud_note)
    download = QPushButton(window.text("Download local model"))
    download.setEnabled(supported() and not ready(window.local_model.root))
    model.model().item(model.findData(LOCAL_MODEL)).setEnabled(ready(window.local_model.root))

    def download_model():
        DownloadDialog(window.local_model.root, window.text, dialog).exec()
        if ready(window.local_model.root):
            download.setEnabled(False)
            model.model().item(model.findData(LOCAL_MODEL)).setEnabled(True)
            model.setCurrentIndex(model.findData(LOCAL_MODEL))
            window.set_analyst_model(LOCAL_MODEL)

    download.clicked.connect(download_model)
    form.addRow(download)
    _buttons(dialog, form, QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        window.change_theme(themes.currentData())
        window.set_cloud_access(cloud_url.text(), cloud_code.text())
        window.set_analyst_model(model.currentData())


def axis_dialog(window):
    dialog = _dialog(window, "Price axis")
    form = QFormLayout(dialog)
    lower_value, upper_value = window.chart.axis_limits or window.chart.price_bounds()
    boxes = []
    for label, value in (("Minimum price", lower_value), ("Maximum price", upper_value)):
        control = QDoubleSpinBox()
        control.setDecimals(12)
        control.setRange(-1e15, 1e15)
        control.setValue(value)
        form.addRow(window.text(label), control)
        boxes.append(control)
    buttons = _buttons(dialog, form, QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    automatic = buttons.addButton(window.text("Automatic axis"), QDialogButtonBox.ButtonRole.ResetRole)
    automatic.clicked.connect(lambda: dialog.done(2))
    result = dialog.exec()
    if result == 2:
        window.chart.set_axis()
    elif result == QDialog.DialogCode.Accepted:
        minimum, maximum = (box.value() for box in boxes)
        if minimum >= maximum:
            window.status.setText(window.text("Maximum price must be greater than minimum price."))
        else:
            window.chart.set_axis(minimum, maximum)


def shortcuts_dialog(window):
    dialog = _dialog(window, "Keyboard shortcuts")
    form = QFormLayout(dialog)
    for keys, label, _ in window.shortcuts:
        form.addRow(QLabel(keys), QLabel(window.text(label)))
    _buttons(dialog, form, QDialogButtonBox.StandardButton.Close)
    dialog.exec()


def social_icon(window, filename):
    file = Path(__file__).with_name("assets") / filename
    foreground = b"white" if QColor(window.chart.colors[1]).lightness() < 128 else b"black"
    contents = file.read_bytes().replace(b'fill="currentColor"', b"")
    contents = contents.replace(b"<path ", b'<path fill="' + foreground + b'" ')
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(contents)).render(painter)
    painter.end()
    return QIcon(pixmap)


def about_dialog(window):
    dialog = _dialog(window, "About Pivluma")
    dialog.setWindowIcon(window.windowIcon())
    available = window.screen().availableGeometry()
    dialog.resize(min(600, available.width() - 48), min(590, available.height() - 80))
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 18, 18, 14)
    scroll = QScrollArea()
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    layout.addWidget(scroll, 1)
    content = QWidget()
    scroll.setWidget(content)
    sections = QVBoxLayout(content)
    sections.setContentsMargins(0, 0, 8, 0)
    sections.setSpacing(12)

    header = QWidget()
    header.setProperty("aboutCard", True)
    branding = QVBoxLayout(header)
    branding.setContentsMargins(16, 14, 16, 14)
    branding.addWidget(window.brand())
    description = QLabel(window.text("A Windows application for market data, forecasting, and local analysis."))
    description.setWordWrap(True)
    branding.addWidget(description)
    sections.addWidget(header)

    system = f"{platform.system()} · {platform.machine()}"
    mode = window.text("Windows package" if getattr(sys, "frozen", False) else "Source run")
    details = QWidget()
    details.setProperty("aboutCard", True)
    grid = QGridLayout(details)
    grid.setContentsMargins(16, 12, 16, 12)
    fields = (("Running version", f"v{__version__}"), ("Platform", system), ("Distribution", mode))
    for index, (key, value) in enumerate(fields):
        grid.addWidget(QLabel(window.text(key)), index, 0)
        field = QLabel(value)
        field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(field, index, 1)
    grid.setColumnStretch(1, 1)
    sections.addWidget(details)

    developer = QWidget()
    developer.setProperty("aboutCard", True)
    developer_row = QHBoxLayout(developer)
    developer_row.setContentsMargins(16, 12, 16, 12)
    identity = QVBoxLayout()
    developer_title = QLabel(window.text("Developer"))
    developer_title.setProperty("sectionTitle", True)
    identity.addWidget(developer_title)
    identity.addWidget(QLabel("Murat Koray Özkan"))
    developer_row.addLayout(identity, 1)
    for network, filename, url, hint in (
        ("GitHub", "mark-github-24.svg", "https://github.com/muratkorayozkan", "Open developer profile on GitHub"),
        ("Instagram", "instagram.svg", "https://www.instagram.com/muratkorayozkan", "Open developer profile on Instagram"),
    ):
        profile = QPushButton(network)
        profile.setIcon(social_icon(window, filename))
        profile.setIconSize(QSize(18, 18))
        profile.setToolTip(window.text(hint))
        profile.setAccessibleName(window.text(hint))
        profile.clicked.connect(lambda checked=False, address=url: QDesktopServices.openUrl(QUrl(address)))
        developer_row.addWidget(profile, alignment=Qt.AlignmentFlag.AlignVCenter)
    sections.addWidget(developer)

    updates = QWidget()
    updates.setProperty("aboutCard", True)
    update_layout = QVBoxLayout(updates)
    update_layout.setContentsMargins(16, 12, 16, 12)
    heading = QLabel(window.text("Updates"))
    heading.setProperty("sectionTitle", True)
    update_layout.addWidget(heading)
    status = QLabel()
    status.setWordWrap(True)
    status.setObjectName("aboutUpdateStatus")
    update_layout.addWidget(status)
    check = QPushButton()
    check.clicked.connect(window.update_button.click)
    update_layout.addWidget(check, alignment=Qt.AlignmentFlag.AlignLeft)
    sections.addWidget(updates)

    links = QGridLayout()
    for index, (label, suffix) in enumerate((
        ("Source code", ""), ("Release notes for this version", f"/releases/tag/v{__version__}"),
        ("Report a problem", "/issues/new"),
    )):
        button = QPushButton(window.text(label))
        button.clicked.connect(lambda checked=False, path=suffix: QDesktopServices.openUrl(QUrl(ROOT + path)))
        links.addWidget(button, index // 2, index % 2)
    sections.addLayout(links)
    sections.addStretch()

    footer = QHBoxLayout()
    layout.addLayout(footer)
    copy = QPushButton(window.text("Copy version details"))
    feedback = QLabel()
    footer.addWidget(copy)
    footer.addWidget(feedback, 1)

    def copy_details():
        text = "\n".join(["Pivluma", *(f"{window.text(key)} · {value}" for key, value in fields)])
        QApplication.clipboard().setText(text)
        feedback.setText(window.text("Version details copied."))

    copy.clicked.connect(copy_details)
    close = QPushButton(window.text("Close dialog"))
    close.clicked.connect(dialog.reject)
    close.setDefault(True)
    footer.addWidget(close)

    def sync_update():
        packaged = not window.update_button.isHidden()
        message = window.update_message or ("Updates have not been checked yet." if packaged else
                                            "Automatic updates are available in the Windows package.")
        status.setText(window.text(message))
        check.setText(window.update_button.text())
        check.setVisible(packaged)
        check.setEnabled(window.update_button.isEnabled())

    sync_update()
    window.update_status_changed.connect(sync_update)
    try:
        dialog.exec()
    finally:
        window.update_status_changed.disconnect(sync_update)
        dialog.deleteLater()


def tradingview_dialog(window):
    symbol = window.symbol.text().strip().upper() or window.series_symbol
    url = tradingview_url(symbol)
    if not url:
        window.status.setText(window.text("Select a valid symbol to open TradingView."))
        return

    dialog = _dialog(window, "TradingView and local forecast")
    dialog.resize(460, 280)
    layout = QVBoxLayout(dialog)
    title = QLabel(window.text("{symbol} · TradingView and local forecast", symbol=symbol))
    title.setProperty("sectionTitle", True)
    layout.addWidget(title)
    note = QLabel(window.text(
        "TradingView opens in your browser for viewing. llama.cpp uses authorized closing history loaded in Pivluma."
    ))
    note.setWordWrap(True)
    layout.addWidget(note)
    chart = QPushButton(window.text("Open TradingView chart"))
    chart.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
    layout.addWidget(chart)
    source = QLabel()
    source.setTextFormat(Qt.TextFormat.PlainText)
    source.setWordWrap(True)
    layout.addWidget(source)
    saved = QPushButton(window.text("Use saved closes for {symbol}", symbol=symbol))
    imported = QPushButton(window.text("Import authorized CSV"))
    run = QPushButton(window.text("Run llama.cpp forecast"))
    layout.addWidget(saved)
    layout.addWidget(imported)
    layout.addWidget(run)

    def update_actions():
        matches = window.series_symbol.upper() == symbol and bool(window.prices)
        if matches:
            message = window.text("Forecast input: {source} · {count} closing prices.",
                                  source=window.text(window.series_source), count=len(window.prices))
        else:
            message = window.text("Load closing history for this symbol from an authorized source.")
        installed = ready(window.local_model.root)
        if not installed:
            message += " " + window.text("Download the local model from Settings to enable llama.cpp.")
        source.setText(message)
        run.setEnabled(matches and installed)

    def load_or_import(action):
        window.symbol.setText(symbol)
        window.status.clear()
        action()
        update_actions()
        if window.status.text():
            source.setText(window.status.text())

    saved.clicked.connect(lambda: load_or_import(lambda: window.load_saved_for_symbol(symbol)))
    imported.clicked.connect(lambda: load_or_import(window.import_csv))

    def start_forecast():
        dialog.accept()
        window.request_llama_forecast()

    run.clicked.connect(start_forecast)
    _buttons(dialog, layout, QDialogButtonBox.StandardButton.Close)
    update_actions()
    window.market.history.connect(update_actions)
    try:
        dialog.exec()
    finally:
        window.market.history.disconnect(update_actions)
        dialog.deleteLater()


def watch_forecasts_dialog(window):
    dialog = _dialog(window, "Watchlist forecasts")
    dialog.resize(770, 485)
    layout = QVBoxLayout(dialog)
    note = QLabel(window.text(
        "The watchlist uses locally recorded outcomes. Open a symbol to refresh its closing history."
    ))
    note.setWordWrap(True)
    layout.addWidget(note)
    columns = ("Symbol", "Latest origin", "Steps", "Final forecast", "Final actual", "Completed", "Pending", "Outside band")
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels([window.text(key) for key in columns])
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.verticalHeader().hide()
    layout.addWidget(table)
    symbols = tuple(window.watchlist)
    try:
        with closing(sqlite3.connect(window.database)) as connection:
            store = ForecastStore(connection)
            if window.series_symbol and window.prices:
                store.reconcile(window.series_symbol, window.prices)
            rows = list(store.watch_rows(symbols))
    except (sqlite3.Error, ValueError):
        rows = []
        note.setText(window.text("Forecast journal unavailable."))
    table.setRowCount(len(rows))
    for index, (symbol, latest, pending, completed, outside, checked) in enumerate(rows):
        origin, _, steps, expected, actual = latest or ("—", "", "—", "—", None)
        values = (symbol, origin, steps, expected, actual if actual is not None else "—",
                  completed, pending, f"{outside}/{checked}" if checked else "—")
        for column, value in enumerate(values):
            table.setItem(index, column, QTableWidgetItem(f"{value:.8g}" if isinstance(value, float) else str(value)))

    def open_symbol(row, _):
        if 0 <= row < len(rows):
            dialog.accept()
            window.open_watch_symbol(rows[row][0])

    table.cellDoubleClicked.connect(open_symbol)
    _buttons(dialog, layout, QDialogButtonBox.StandardButton.Close)
    try:
        dialog.exec()
    finally:
        dialog.deleteLater()


def history_dialog(window):
    dialog = _dialog(window, "Revision history")
    dialog.resize(680, 520)
    layout = QVBoxLayout(dialog)
    notes = QPlainTextEdit()
    notes.setReadOnly(True)
    notes.setPlainText(window.text("Loading release notes…"))
    layout.addWidget(notes)
    link = QPushButton(window.text("View all releases on GitHub"))
    link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(ROOT + "/releases")))
    layout.addWidget(link)
    _buttons(dialog, layout, QDialogButtonBox.StandardButton.Close)

    network = QNetworkAccessManager(dialog)
    request = QNetworkRequest(QUrl("https://api.github.com/repos/muratkorayozkan/Pivluma/releases?per_page=20"))
    request.setRawHeader(b"User-Agent", b"Pivluma")
    request.setTransferTimeout(8000)
    reply = network.get(request)

    def loaded():
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError()
            releases = json.loads(bytes(reply.readAll()))
            entries = [
                f"{release['tag_name']}  ·  {(release.get('published_at') or '')[:10]}\n"
                f"{release.get('body') or ''}"
                for release in releases if not release.get("draft")
            ]
            notes.setPlainText("\n\n".join(entries) or window.text("No releases found."))
        except (AttributeError, KeyError, TypeError, ValueError):
            notes.setPlainText(window.text("Release notes unavailable. View them on GitHub."))

    reply.finished.connect(loaded)
    dialog.exec()
    if not reply.isFinished():
        reply.abort()
    dialog.deleteLater()
