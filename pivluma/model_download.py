"""Shared local model download window for Settings and Windows Setup."""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout

from pivluma.local_assets import MODEL, RUNTIME, install, ready


class DownloadThread(QThread):
    progress = Signal(int)

    def __init__(self, root, parent):
        super().__init__(parent)
        self.root = root
        self.success = False

    def run(self):
        try:
            install(self.root, self.progress.emit, self.isInterruptionRequested)
            self.success = True
        except (OSError, ValueError):
            pass


class DownloadDialog(QDialog):
    def __init__(self, root, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(text("Local model download"))
        self.resize(420, 160)
        self.text = text
        layout = QVBoxLayout(self)
        self.status = QLabel(text("Download llama.cpp and Qwen3 (1.31 GB). Allow 2 GB of free disk space."))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, RUNTIME[1] + MODEL[1])
        layout.addWidget(self.progress)
        self.button = QPushButton(text("Download"))
        layout.addWidget(self.button)
        self.worker = DownloadThread(root, self)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished.connect(self.finished_download)
        self.button.clicked.connect(self.start)
        if ready(root):
            self.worker.success = True
            self.finished_download()

    def start(self):
        self.button.clicked.disconnect(self.start)
        self.button.clicked.connect(self.reject)
        self.button.setText(self.text("Cancel"))
        self.status.setText(self.text("Downloading and verifying the local model…"))
        self.worker.start()

    def reject(self):
        if self.worker.isRunning():
            self.button.setEnabled(False)
            self.worker.requestInterruption()
            self.status.setText(self.text("Cancelling download…"))
            return
        super().reject()

    def finished_download(self):
        if self.worker.isInterruptionRequested() and not self.worker.success:
            super().reject()
            return
        if self.worker.success:
            self.progress.setValue(self.progress.maximum())
        message = "Local model is ready." if self.worker.success else "Download failed. Check the connection and available disk space."
        self.status.setText(self.text(message))
        self.button.setText(self.text("Close"))
        self.button.clicked.disconnect()
        self.button.clicked.connect(self.accept if self.worker.success else self.reject)
        self.button.setEnabled(True)
