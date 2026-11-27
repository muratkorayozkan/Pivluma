"""Background update controls for the packaged desktop application."""

from pathlib import Path
from threading import Thread
from zipfile import BadZipFile

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from pivluma import __version__
from pivluma.updates import VERSION, check_update, prepare_update, update_error_message


class UpdateController(QObject):
    completed = Signal(str, object)

    def __init__(self, window, button, directory: Path, settings):
        super().__init__(window)
        self.window = window
        self.button = button
        self.directory = directory
        self.settings = settings
        self.pending = None
        self.button.clicked.connect(self.activate)
        self.completed.connect(self.finished)
        QTimer.singleShot(1000, self, self.start)

    @staticmethod
    def preferred_executable(settings, directory: Path):
        name = settings.value("preferred_release", "", type=str)
        version, separator, identifier = name.partition("-")
        if (
            not separator or not VERSION.fullmatch(version)
            or len(identifier) != 32 or any(char not in "0123456789abcdef" for char in identifier)
            or tuple(map(int, version.split("."))) <= tuple(map(int, __version__.split(".")))
        ):
            return None
        path = directory / name / "Pivluma.exe"
        if path.is_file() and path.resolve().is_relative_to(directory.resolve()):
            return path
        return None

    def set_status(self, message):
        self.window.update_message = message
        self.window.update_label.setText(self.window.text(message))

    def start(self):
        if not self.button.isEnabled() or self.pending is not None:
            return
        self.button.setEnabled(False)
        self.set_status("Checking and preparing updates…")
        Thread(target=self.work, daemon=True).start()

    def work(self):
        try:
            update = check_update(__version__)
            if update is None:
                self.completed.emit("No newer verified update found.", None)
                return
            path = prepare_update(update, self.directory)
        except (AttributeError, BadZipFile, OSError, TypeError, ValueError) as error:
            self.completed.emit(update_error_message(error), None)
            return
        self.completed.emit("Update ready.", (update.version, path))

    def finished(self, message, pending):
        self.pending = pending
        self.set_status(message)
        if pending is not None:
            self.window.localize(self.button.setText, "Open update")
        self.button.setEnabled(True)

    def activate(self):
        if self.pending is None:
            self.start()
            return
        version, path = self.pending
        answer = QMessageBox.question(
            self.window, self.window.text("Pivluma update"),
            self.window.text("Version {version} is ready. Open it now?", version=version),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        started, _ = QProcess.startDetached(str(path), [], str(path.parent))
        if started:
            self.settings.setValue("preferred_release", path.parent.name)
            self.settings.sync()
            self.window.close()
        else:
            self.set_status("Could not start the new version.")
