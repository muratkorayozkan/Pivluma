"""Entry point for the packaged Windows application."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from pivluma.desktop import Dashboard, main
from pivluma.updates import check_update, prepare_update


def smoke_test() -> int:
    application = QApplication(["Pivluma"])
    window = Dashboard(Path("pivluma.sqlite3"))
    window.show()
    application.processEvents()
    try:
        window.horizon.setValue(7)
        window.question.setText("What is the forecast?")
        window.ask_analyst()
        english_reply = window.reply.text()
        window.language_picker.setCurrentIndex(1)
        window.question.setText("Tahmin ne?")
        window.ask_analyst()
        return int(
            window.windowIcon().isNull() or window.chart.grab().isNull()
            or "7 observations ahead" not in english_reply or "7 gözlem" not in window.reply.text()
        )
    finally:
        window.close()
        delete(window)
        application.processEvents()


if __name__ == "__main__":
    if sys.argv[1:] == ["--smoke-test"]:
        raise SystemExit(smoke_test())
    if sys.argv[1:] == ["--smoke-update"]:
        update = check_update("0.4.0")
        if update is None:
            raise SystemExit(1)
        with TemporaryDirectory() as directory:
            raise SystemExit(0 if prepare_update(update, Path(directory)).is_file() else 1)
    raise SystemExit(main())
