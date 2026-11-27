import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
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

    def test_switching_to_sample_stops_live_feed_and_clears_quote(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.live_symbol = "AAPL"
            window.receive_history("AAPL", sample_prices())
            window.receive_quote(123.45, datetime.now(timezone.utc))
            self.assertIn("AAPL", window.quote_label.text())
            with patch.object(window.market, "stop") as stop:
                window.show_sample()
                stop.assert_called_once()
            self.assertEqual(window.quote_label.text(), "")
            self.assertIn("Synthetic sample", window.dataset.text())
        finally:
            window.close()

    def test_disconnecting_keeps_last_quote_with_timestamp(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.live_symbol = "AAPL"
            now = datetime.now(timezone.utc)
            window.receive_quote(123.45, now)
            window.receive_quote(100, now - timedelta(seconds=1))
            window.stop_market()
            self.assertIn("123.45", window.quote_label.text())
            self.assertIn("UTC", window.quote_label.text())
            self.assertIn("Last received quote", window.quote_label.text())
        finally:
            window.close()

    def test_live_symbol_list_stops_before_prompting_for_api_key(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.symbol.setText("AAPL,MSFT")
            with patch("pivluma.desktop.QInputDialog.getText", return_value=("", False)) as prompt:
                window.connect_market()
            prompt.assert_not_called()
            self.assertIn("one symbol", window.status.text())
            self.assertIn("Disconnected", window.market_status.text())
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
