import sqlite3
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from pivluma.data import Price, sample_prices
from pivluma.desktop import Dashboard
from pivluma.headlines import HeadlineFeed, parse_articles
from pivluma.lab import ForecastStore
from pivluma.research_store import ResearchStore, breached_runs, performance_rows
from pivluma.research_ui import ResearchWorkspace


class ResearchStoreTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.day = date(2026, 1, 5)

    def test_replay_uses_only_snapshots_known_before_origin(self):
        store = ResearchStore(self.connection)
        yesterday = self.day - timedelta(days=1)
        prior = datetime(2026, 1, 4, 12, tzinfo=timezone.utc)
        same_day = prior + timedelta(days=1)
        store.save_scores("SPY", "Prior", {yesterday: .2, self.day: .8}, captured=prior)
        store.save_scores("SPY", "Revised", {yesterday: -.9}, captured=same_day)
        self.assertEqual(store.scores_at("SPY", self.day), ("Prior", {yesterday: .2}))
        self.assertEqual(store.scores_at("QQQ", self.day), ("", {}))
        self.assertEqual(store.scores_at("SPY", self.day + timedelta(days=1)), ("Revised", {yesterday: -.9}))

    def test_prospective_performance_and_breach_deduplication(self):
        store = ForecastStore(self.connection)
        next_day = self.day + timedelta(days=1)
        store.save("SPY", self.day, "llama.cpp", (104,), origin_close=100, bands=[(100, 108)], recorded_on=self.day)
        store.save("SPY", self.day, "llama.cpp", (120,), origin_close=100, bands=[(110, 130)], recorded_on=self.day)
        store.save("SPY", self.day, "llama.cpp", (112, 114), origin_close=100, recorded_on=next_day + timedelta(days=1))
        store.reconcile("SPY", [Price(self.day, 100), Price(next_day, 110),
                                Price(next_day + timedelta(days=1), 115)])
        self.assertEqual(performance_rows(self.connection, "SPY"), [(1, 1, 6, 10, 1, 0, 1)])
        breaches = breached_runs(self.connection, "SPY", next_day)
        self.assertEqual(len(breaches), 1)
        alerts = ResearchStore(self.connection)
        self.assertEqual(alerts.register_events("SPY", "band", breaches), (breaches, False))
        self.assertEqual(alerts.register_events("SPY", "band", breaches), ([], True))

    def test_headlines_use_provider_seen_time_and_validate_links(self):
        now = datetime(2026, 1, 5, 13, tzinfo=timezone.utc)
        valid = {"url": "https://publisher.example/story", "title": "Title", "seendate": "20260105T121500Z"}
        invalid = [dict(valid, url="javascript:alert(1)"), dict(valid, seendate="20270105T121500Z"),
                   dict(valid, url="https://user@publisher.example/story")]
        parsed = parse_articles({"articles": [valid, valid, *invalid]}, now)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].source, "publisher.example")
        self.assertEqual(parsed[0].seen.hour, 12)


class ResearchWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.window = Dashboard(Path(temporary.name) / "research.sqlite3")
        self.addCleanup(self.window.close)
        self.window.set_series("Sample prices", sample_prices(), "DEMO")

    def test_replay_hides_unseen_closes_and_does_not_use_later_news(self):
        dialog = ResearchWorkspace(self.window)
        self.addCleanup(dialog.close)
        dialog.engine.setCurrentIndex(0)
        dialog.origin.setCurrentIndex(dialog.origin.count() - 8)
        count = dialog.origin.currentData()
        dialog.horizon.setValue(3)
        origin = dialog.prices[count - 1].day
        with closing(sqlite3.connect(dialog.database)) as connection:
            ResearchStore(connection).save_scores("DEMO", "LATE", {origin - timedelta(days=1): 1.0},
                                                   captured=datetime.combine(origin, datetime.min.time(), timezone.utc))
        dialog.generate_replay()
        self.assertEqual(len(dialog.replay_chart.history), count)
        self.assertEqual(len(dialog.replay_chart.llama_forecast), 3)
        self.assertIn("prices only", dialog.replay_status.text())
        self.assertEqual(dialog.steps.item(0, 3).text(), "Pending")
        dialog.advance_replay()
        self.assertEqual(dialog.replay_chart.replay_closes, (dialog.prices[count].close,))
        self.assertEqual(dialog.steps.item(1, 3).text(), "Pending")

    def test_rate_limited_headline_response_sets_retry_window(self):
        feed = HeadlineFeed(self.window)
        messages = []
        feed.failed.connect(messages.append)
        reply = Mock()
        reply.attribute.return_value = 429
        reply.rawHeader.return_value = b"240"
        feed.pending = reply
        with patch("pivluma.headlines.monotonic", return_value=100), patch.object(HeadlineFeed, "retry_at", 0):
            feed.finish(reply, "BTC/USD")
            self.assertEqual(HeadlineFeed.retry_at, 340)
        reply.rawHeader.assert_called_once_with("Retry-After")
        self.assertEqual(messages, ["Headlines are temporarily unavailable."])

    def test_price_initial_value_seeds_threshold_and_crossing_notifies_once(self):
        alerts = self.window.watch_alerts
        self.window.watchlist = ["DEMO"]
        alerts.config = {"DEMO": {"threshold": 100}}
        with patch.object(alerts, "notify") as notify:
            for price in (99, 101, 102, 99, 101):
                alerts.price("DEMO", price, quote=True)
            self.assertEqual(notify.call_count, 1)
