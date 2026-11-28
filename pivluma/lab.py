"""Causal forecast replays and persistent forecast outcomes."""
import json
from statistics import mean

from pivluma.forecast import TRAINING_WINDOW, analyze


def replay_origins(prices, horizon, limit=12):
    available = range(len(prices) - horizon, TRAINING_WINDOW + horizon + 8, -max(1, horizon))
    return sorted(list(available)[:limit])


def replay(prices, horizon, scores, alignment):
    closes = [price.close for price in prices]
    rows = []
    for origin in replay_origins(prices, horizon):
        history = prices[:origin]
        news = alignment(history, scores) if scores else None
        baseline = analyze(closes[:origin], horizon)
        research = analyze(closes[:origin], horizon, news) if news else baseline
        rows.append((history[-1].day, closes[origin + horizon - 1], closes[origin - 1],
                     baseline.forecast[-1], research.forecast[-1]))
    return rows


def terminal_mae(rows, index):
    return mean(abs(row[index] - row[1]) for row in rows) if rows else None


class ForecastStore:
    def __init__(self, connection):
        self.connection = connection
        connection.execute("""
            CREATE TABLE IF NOT EXISTS forecast_runs (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                origin TEXT NOT NULL,
                engine TEXT NOT NULL,
                predicted TEXT NOT NULL,
                observed TEXT NOT NULL DEFAULT '[]',
                UNIQUE(symbol, origin, engine, predicted)
            )
        """)

    def save(self, symbol, origin, engine, path):
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO forecast_runs(symbol, origin, engine, predicted) VALUES (?, ?, ?, ?)",
                (symbol, origin.isoformat(), engine, json.dumps(list(path), allow_nan=False)),
            )

    def reconcile(self, symbol, prices):
        rows = self.connection.execute(
            "SELECT id, origin, predicted, observed FROM forecast_runs WHERE symbol = ?", (symbol,),
        ).fetchall()
        series = [(price.day.isoformat(), price.close) for price in prices]
        with self.connection:
            for row_id, origin, predicted, observed in rows:
                existing = dict(json.loads(observed))
                if not series:
                    continue
                if series[0][0] > origin and not any(day in existing for day, _ in series):
                    continue
                limit = len(json.loads(predicted))
                existing.update((day, value) for day, value in series if day > origin)
                updated = sorted(existing.items())[:limit]
                self.connection.execute("UPDATE forecast_runs SET observed = ? WHERE id = ?",
                                        (json.dumps(updated), row_id))

    def runs(self, symbol):
        data = self.connection.execute(
            "SELECT origin, engine, predicted, observed FROM forecast_runs WHERE symbol = ? "
            "ORDER BY origin DESC, id DESC LIMIT 100", (symbol,),
        )
        return [(origin, engine, json.loads(predicted), json.loads(observed))
                for origin, engine, predicted, observed in data]
