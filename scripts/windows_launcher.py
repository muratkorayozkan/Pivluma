"""Entry point for the packaged Windows application."""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pivluma.desktop import Dashboard, main


def smoke_test() -> int:
    application = QApplication(["Pivluma"])
    window = Dashboard(Path("pivluma.sqlite3"))
    window.show()
    application.processEvents()
    try:
        window.horizon.setValue(7)
        window.question.setText("What is the forecast?")
        window.ask_analyst()
        return int(window.chart.grab().isNull() or "7 observations ahead" not in window.reply.text())
    finally:
        window.close()


if __name__ == "__main__":
    raise SystemExit(smoke_test() if sys.argv[1:] == ["--smoke-test"] else main())
