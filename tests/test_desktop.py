import csv
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from shiboken6 import delete
except ModuleNotFoundError as error:
    if error.name != "PySide6":
        raise
    raise unittest.SkipTest("Install requirements-desktop.txt to run Qt tests.")

from pivluma.data import PriceStore, sample_prices
from pivluma.desktop import Dashboard
from pivluma.update_ui import UpdateController


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
            delete(window)
            self.application.processEvents()

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
            delete(window)
            self.application.processEvents()

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
            delete(window)
            self.application.processEvents()

    def test_empty_live_symbol_opens_inline_list_and_selection_uses_code(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            with patch.object(window.symbol_picker, "showPopup") as picker:
                window.connect_market()
            picker.assert_called_once()
            index = window.symbol_picker.findData("BTC/USD")
            self.assertEqual(window.symbol_picker.itemText(index), "Bitcoin (BTC/USD)")
            window.symbol_picker.setCurrentIndex(index)
            window.symbol_picker.activated.emit(index)
            self.assertEqual(window.symbol.text(), "BTC/USD")
            with patch.object(window.market, "start") as connect:
                window.connect_market()
            connect.assert_called_once_with("BTC/USD", None)
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

    def test_stock_uses_configured_access_without_prompting(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.symbol.setText("aapl")
            with patch.dict("os.environ", {"TWELVE_DATA_API_KEY": "test-key"}), patch(
                "pivluma.desktop.QInputDialog.getText"
            ) as prompt, patch.object(window.market, "start") as connect:
                window.connect_market()
            prompt.assert_not_called()
            connect.assert_called_once_with("AAPL", "test-key")
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

    def test_analyst_keeps_calculated_facts_when_local_model_is_unavailable(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.set_analyst_model("qwen3:4b")
            with patch.object(window.local_model, "start") as start:
                window.ask_topic("What is the forecast?")
            args = start.call_args.args
            self.assertEqual(args[0], "qwen3:4b")
            self.assertEqual(args[3]["forecast"][0]["price"], window.chart.result.forecast[0])
            self.assertEqual(args[3]["selected_forecast_model"], window.chart.result.model)
            window.show_model_answer("qwen3:4b", "")
            self.assertIn("5 observations ahead", window.reply.text())
            self.assertIn("Local model unavailable", window.reply.text())
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

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
            delete(window)
            self.application.processEvents()

    def test_export_csv_uses_the_active_forecast_horizon(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forecast.csv"
            window = Dashboard(Path(directory) / "unused.sqlite3")
            try:
                window.horizon.setValue(3)
                with patch("pivluma.desktop.QFileDialog.getSaveFileName",
                           return_value=(str(path), "")):
                    window.export_forecast()
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual([row["step"] for row in rows], ["1", "2", "3"])
                self.assertEqual(rows[0]["last_observed_date"], str(window.prices[-1].day))
                self.assertEqual(float(rows[-1]["forecast"]), window.chart.result.forecast[-1])
                self.assertEqual(float(rows[-1]["lower"]), window.chart.result.lower[-1])
                self.assertEqual(float(rows[-1]["upper"]), window.chart.result.upper[-1])
            finally:
                window.close()
            delete(window)
            self.application.processEvents()

    def test_language_switch_preserves_analysis_and_changes_visible_text(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.horizon.setValue(7)
            result = window.chart.result
            window.language_picker.setCurrentIndex(1)
            self.assertIn("Sentetik örnek", window.dataset.text())
            self.assertIn("Son kapanış", window.metrics.text())
            self.assertIs(window.chart.result, result)
            self.assertEqual(window.horizon.value(), 7)
            window.question.setText("Tahmin ne?")
            window.ask_analyst()
            self.assertIn("7 gözlem", window.reply.text())
            window.language_picker.setCurrentIndex(0)
            self.assertIn("Synthetic sample", window.dataset.text())
            self.assertFalse(window.windowIcon().isNull())
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

    def test_update_handoff_requires_confirmation_and_remembers_new_version(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            settings = Mock()
            controller = UpdateController(window, window.update_button, Path("Versions"), settings)
            path = Path("Versions/0.6.0-0123456789abcdef0123456789abcdef/Pivluma.exe")
            controller.finished("Update ready.", ("0.6.0", path))
            self.assertIn("Update ready", window.update_label.text())
            with patch("pivluma.update_ui.QProcess.startDetached", return_value=(True, 123)) as launch:
                with patch("pivluma.update_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
                    controller.activate()
                launch.assert_not_called()
                with patch("pivluma.update_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                    controller.activate()
                launch.assert_called_once_with(str(path), [], str(path.parent))
            settings.setValue.assert_called_once_with("preferred_release", path.parent.name)
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

    def test_compact_dashboard_keeps_the_chart_and_text_separate(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.update_button.show()
            window.update_label.setText("Checking and preparing updates…")
            window.update_label.show()
            window.language_picker.setCurrentIndex(1)
            window.set_history_warning("Twelve Data · History request failed. Check connection and account access.")
            window.ask_topic("What is the forecast?")
            window.resize(760, 680)
            window.show()
            self.application.processEvents()
            chart = window.chart.geometry()
            self.assertLess(chart.bottom(), window.chart_legend.geometry().top())
            self.assertLess(chart.bottom(), window.metrics.geometry().top())
            self.assertGreater(window.scroll_area.verticalScrollBar().maximum(), 0)
            window.scroll_area.ensureWidgetVisible(window.metrics)
            self.assertGreater(window.scroll_area.verticalScrollBar().value(), 0)
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

    def test_extended_horizon_and_chart_selection(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            self.assertEqual(window.horizon.maximum(), 91)
            window.horizon.setValue(80)
            self.assertEqual(len(window.chart.result.forecast), 80)
            window.chart.set_history_limit(30)
            window.show()
            self.application.processEvents()
            area = window.chart.plot_area
            x = area.left() + area.width() * 29 / 109
            QTest.mouseClick(window.chart, Qt.MouseButton.LeftButton, pos=QPoint(round(x), round(area.center().y())))
            self.assertIn(str(window.prices[-1].day), window.point_details.text())
            window.chart.select_index(30)
            self.assertIn("Forecast step 1", window.point_details.text())
            window.chart.set_axis(50, 150)
            self.assertEqual(window.chart.axis_limits, (50, 150))
            window.chart.set_axis()
            self.assertIsNone(window.chart.axis_limits)
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

    def test_theme_updates_chart_and_update_status_is_below_the_button(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.settings = Mock()
            original_colors = window.chart.colors
            window.change_theme("light-mint")
            self.assertNotEqual(window.chart.colors, original_colors)
            window.settings.setValue.assert_called_once_with("theme", "light-mint")
            window.update_button.show()
            window.update_label.setText("Update ready.")
            window.update_label.show()
            window.show()
            self.application.processEvents()
            self.assertLess(window.update_button.geometry().bottom(), window.update_label.geometry().top())
            self.assertEqual(window.update_button.geometry().right(), window.update_label.geometry().right())
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

    def test_quote_timestamp_uses_local_time_with_offset(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.live_symbol = "BTC/USD"
            instant = datetime(2026, 11, 27, 17, 27, tzinfo=timezone.utc)
            window.receive_quote(100, instant)
            self.assertIn(instant.astimezone().strftime("%Y-%m-%d %H:%M:%S UTC%z"),
                          window.quote_label.text())
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

    def test_workspace_reflows_and_keeps_the_same_chart(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.resize(1320, 900)
            window.show()
            self.application.processEvents()
            self.assertTrue(window.workspace_splitter.isVisible())
            result = window.chart.result
            window.resize(760, 680)
            self.application.processEvents()
            self.assertTrue(window.workspace_tabs.isVisible())
            self.assertIs(window.chart.result, result)
            window.workspace_tabs.setCurrentIndex(1)
            self.assertTrue(window.reply.isVisible())
            window.resize(1320, 900)
            self.application.processEvents()
            self.assertTrue(window.chart.isVisible())
            self.assertTrue(window.reply.isVisible())
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

    def test_chart_viewport_and_analyst_context(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            chart = window.chart
            chart.set_history_limit(30)
            chart.pan_history(20)
            self.assertEqual(len(chart.visible_history()), 30)
            self.assertEqual(chart.visible_days()[-1], window.prices[-21].day)
            self.assertFalse(chart.forecast_visible())
            window.ask_topic("Summarize the visible period")
            self.assertIn(str(window.prices[-21].day), window.reply.text())
            chart.select_index(0)
            window.ask_topic("Explain the selected point")
            self.assertIn(str(chart.visible_days()[0]), window.reply.text())
            chart.reset_view()
            self.assertTrue(chart.forecast_visible())
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

    def test_chart_mouse_wheel_and_drag_change_the_visible_period(self):
        window = Dashboard(Path("unused.sqlite3"))
        try:
            window.show()
            self.application.processEvents()
            chart = window.chart
            center = chart.plot_area.center().toPoint()
            wheel = QWheelEvent(
                QPointF(center), QPointF(chart.mapToGlobal(center)), QPoint(), QPoint(0, 120),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
            )
            QApplication.sendEvent(chart, wheel)
            self.assertLess(len(chart.visible_history()), len(window.prices))
            QTest.mousePress(chart, Qt.MouseButton.LeftButton, pos=center)
            QTest.mouseMove(chart, center + QPoint(80, 0))
            QTest.mouseRelease(chart, Qt.MouseButton.LeftButton, pos=center + QPoint(80, 0))
            self.assertGreater(chart.history_offset, 0)
            self.assertIsNone(chart.selected_index)
            self.assertFalse(chart.forecast_visible())
        finally:
            window.close()
            delete(window)
            self.application.processEvents()

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
                with closing(sqlite3.connect(database)) as connection:
                    PriceStore(connection).replace("ALPHA", rows)
                window.show_sample()
                window.symbol.clear()
                with patch("pivluma.desktop.QInputDialog.getItem",
                           return_value=("DEMO", True)) as picker:
                    window.load_saved()
                self.assertEqual(picker.call_args.args[3], ["ALPHA", "DEMO"])
                self.assertEqual(window.symbol.text(), "DEMO")
                self.assertIn("Stored CSV", window.dataset.text())

                window.show_sample()
                with patch("pivluma.desktop.QInputDialog.getItem",
                           return_value=("ALPHA", False)):
                    window.load_saved()
                self.assertIn("Synthetic sample", window.dataset.text())
            finally:
                window.close()
            delete(window)
            self.application.processEvents()
