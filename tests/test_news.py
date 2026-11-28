import random
import unittest
from datetime import date, timedelta
from io import StringIO

try:
    from pivluma.news import align_scores, read_news_scores, timeline_scores
except ModuleNotFoundError as error:
    if not (error.name or "").startswith("PySide6"):
        raise
    raise unittest.SkipTest("Install requirements-desktop.txt to run news tests.")

from pivluma.data import Price
from pivluma.forecast import NEWS_MODEL, analyze


class NewsTests(unittest.TestCase):
    def test_only_prior_calendar_day_is_available_at_a_close(self):
        day = date(2026, 1, 2)
        rows = [Price(day, 100)]
        self.assertEqual(align_scores(rows, {day: 1, day - timedelta(days=1): -.5}), [-.5])
        self.assertEqual(align_scores(rows, {day: 1}), [None])

    def test_gdelt_and_csv_scores_use_a_bounded_scale(self):
        payload = {"timeline": [{"data": [{"date": "20260101T000000Z", "value": "2.5"}]}]}
        self.assertEqual(timeline_scores(payload), read_news_scores(StringIO("date,tone\n2026-01-01,2.5\n")))
        with self.assertRaises(ValueError):
            read_news_scores(StringIO("date,tone\n2026-01-01,nan\n"))

    def test_walk_forward_can_select_news_when_it_predicts_returns(self):
        randomizer = random.Random(42)
        tones = [randomizer.choice((-1., 1.)) for _ in range(120)]
        prices = [100.]
        for tone in tones[:-1]:
            prices.append(prices[-1] * (1 + .012 * tone))
        pure = analyze(prices, 1)
        with_news = analyze(prices, 1, tones)
        self.assertEqual(with_news.model, NEWS_MODEL)
        self.assertGreater(with_news.news_pairs, 20)
        self.assertLess(with_news.mae, pure.mae)
        self.assertNotEqual(with_news.forecast, pure.forecast)

    def test_missing_current_news_preserves_a_price_based_forecast(self):
        prices = [100 + index for index in range(45)]
        result = analyze(prices, 3, [1.] * 44 + [None])
        self.assertEqual(result.model, "Linear trend")
        self.assertEqual(result.forecast, (145., 146., 147.))
