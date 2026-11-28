"""Desktop dialogs for appearance, chart limits, and published release notes."""

import json

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from pivluma import __version__
from pivluma.appearance import THEMES
from pivluma.local_assets import ready, supported
from pivluma.local_model import LOCAL_MODEL
from pivluma.model_download import DownloadDialog
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


def about_dialog(window):
    dialog = _dialog(window, "About Pivluma")
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(f"Pivluma {__version__}"))
    description = QLabel(window.text("A Windows application for market data, forecasting, and local analysis."))
    description.setWordWrap(True)
    layout.addWidget(description)
    project = QPushButton("GitHub")
    project.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(ROOT)))
    layout.addWidget(project)
    _buttons(dialog, layout, QDialogButtonBox.StandardButton.Close)
    dialog.exec()


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
