import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as error:
    if error.name != "PySide6":
        raise
    raise unittest.SkipTest("Install requirements-desktop.txt to run Qt tests.")

from pivluma.data import PriceStore, sample_prices
from pivluma.desktop import Dashboard


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_sample_draws_and_horizon_refreshes(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.show()
            self.application.processEvents()
            self.assertFalse(window.chart.grab().isNull())
            self.assertIn("Synthetic sample", window.dataset.text())
            window.horizon.setValue(10)
            self.assertEqual(len(window.chart.result.forecast), 10)
            self.assertIn("Last-price MAE", window.metrics.text())
            window.question.setText("What is the forecast?")
            window.question.returnPressed.emit()
            self.assertIn("10 observations ahead", window.reply.text())
            window.horizon.setValue(3)
            self.assertEqual(window.reply.text(), "")
            window.question.returnPressed.emit()
            self.assertIn("3 observations ahead", window.reply.text())
        finally:
            window.close()

    def test_csv_import_persists_and_saved_series_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            file = path / "prices.csv"
            rows = sample_prices()[:30]
            file.write_text("date,close\n" + "\n".join(
                f"{row.day},{row.close}" for row in rows), encoding="utf-8")
            database = path / "prices.sqlite3"
            window = Dashboard(database)
            try:
                window.symbol.setText("DEMO")
                with patch("pivluma.desktop.QFileDialog.getOpenFileName",
                           return_value=(str(file), "")):
                    window.import_csv()
                self.assertIn("Imported CSV", window.dataset.text())
                self.assertEqual(window.horizon.maximum(), 1)
                with closing(sqlite3.connect(database)) as connection:
                    self.assertEqual(PriceStore(connection).load("DEMO"), rows)
                window.show_sample()
                window.load_saved()
                self.assertIn("Stored CSV", window.dataset.text())
            finally:
                window.close()
