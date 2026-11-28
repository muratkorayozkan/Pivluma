"""Persistent research context and point-in-time evaluation."""
import json
from datetime import date, datetime, timezone
from statistics import mean


class ResearchStore:
    def __init__(self, connection):
        self.connection = connection
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS research_scores (
                symbol TEXT NOT NULL, captured TEXT NOT NULL, source TEXT NOT NULL, scores TEXT NOT NULL,
                PRIMARY KEY(symbol, captured)
            );
            CREATE TABLE IF NOT EXISTS alert_seen (
                symbol TEXT NOT NULL, kind TEXT NOT NULL, event TEXT NOT NULL,
                PRIMARY KEY(symbol, kind, event)
            );
        """)

    def save_scores(self, symbol, source, scores, *, captured=None):
        if not scores or not symbol:
            return
        captured = captured or datetime.now(timezone.utc)
        data = json.dumps({str(day): score for day, score in scores.items()}, allow_nan=False, sort_keys=True)
        with self.connection:
            latest = self.connection.execute(
                "SELECT source, scores FROM research_scores WHERE symbol = ? AND captured <= ? "
                "ORDER BY captured DESC LIMIT 1", (symbol, captured.isoformat())
            ).fetchone()
            if latest == (source, data):
                return
            self.connection.execute("INSERT OR REPLACE INTO research_scores VALUES (?, ?, ?, ?)",
                                    (symbol, captured.isoformat(), source, data))

    def scores_at(self, symbol, day):
        # A capture from the origin day may have happened after that market closed.
        row = self.connection.execute(
            "SELECT source, scores FROM research_scores WHERE symbol = ? AND captured < ? "
            "ORDER BY captured DESC LIMIT 1", (symbol, day.isoformat())
        ).fetchone()
        if row is None:
            return "", {}
        source, payload = row
        return source, {date.fromisoformat(key): score for key, score in json.loads(payload).items()
                        if key < day.isoformat()}

    def register_events(self, symbol, kind, events):
        """Record observations, returning new ones and whether a prior baseline exists."""
        with self.connection:
            known = self.connection.execute(
                "SELECT 1 FROM alert_seen WHERE symbol = ? AND kind = ? LIMIT 1", (symbol, kind)
            ).fetchone() is not None
            new = []
            for event in events:
                cursor = self.connection.execute("INSERT OR IGNORE INTO alert_seen VALUES (?, ?, ?)",
                                                 (symbol, kind, event))
                if cursor.rowcount:
                    new.append(event)
        return new, known


def performance_rows(connection, symbol):
    """One prospective llama run per origin and horizon, against the same outcomes."""
    rows = connection.execute(
        "SELECT origin, predicted, observed, origin_close, recorded_on, bands FROM forecast_runs "
        "WHERE symbol = ? AND engine = 'llama.cpp' AND origin_close > 0 AND recorded_on IS NOT NULL "
        "ORDER BY origin, id", (symbol,)
    )
    buckets = {}
    for origin, predicted, observed, close, recorded, bands in rows:
        path, actual = json.loads(predicted), json.loads(observed)
        if not path or len(actual) < len(path) or recorded >= actual[-1][0]:
            continue
        intervals = json.loads(bands) if bands else None
        bucket = buckets.setdefault(len(path), {})
        bucket.setdefault(origin, (path[-1], actual[-1][1], close, intervals[-1] if intervals else None))
    result = []
    for horizon, cases in sorted(buckets.items()):
        values = list(cases.values())
        with_bands = [(actual, bounds) for _, actual, _, bounds in values if bounds is not None]
        correct = sum(((prediction > close) - (prediction < close)) == ((actual > close) - (actual < close))
                      for prediction, actual, close, _ in values)
        covered = sum(bounds[0] <= actual <= bounds[1] for actual, bounds in with_bands)
        result.append((horizon, len(values),
                       mean(abs(prediction - actual) for prediction, actual, _, _ in values),
                       mean(abs(close - actual) for _, actual, close, _ in values),
                       correct, covered, len(with_bands)))
    return result


def breached_runs(connection, symbol, day):
    rows = connection.execute(
        "SELECT id, observed, bands, recorded_on FROM forecast_runs "
        "WHERE symbol = ? AND engine = 'llama.cpp' AND bands IS NOT NULL AND recorded_on < ?",
        (symbol, day.isoformat())
    )
    key = day.isoformat()
    return [f"{row_id}:{key}" for row_id, observed, bands, _ in rows
            if any(stamp == key and bounds is not None and not bounds[0] <= actual <= bounds[1]
                   for (stamp, actual), bounds in zip(json.loads(observed), json.loads(bands) or ()))]
