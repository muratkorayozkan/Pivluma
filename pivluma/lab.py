"""Causal forecast replays and persistent forecast outcomes."""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
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


MIN_CALIBRATION = 20


@dataclass(frozen=True)
class CalibratedBand:
    lower: float
    upper: float
    samples: int
    covered: int
    checked: int


def _radius(errors):
    return sorted(errors)[ceil(.9 * len(errors)) - 1]


def paired_input_errors(outcomes, variants):
    complete = [row for row in outcomes.values() if all(variant in row[2] for variant in variants)]
    if not complete:
        return 0, None, {}
    flat = mean(abs(actual - previous) for actual, previous, _ in complete)
    errors = {
        variant: mean(abs(predictions[variant] - actual) for actual, _, predictions in complete)
        for variant in variants
    }
    return len(complete), flat, errors


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
                origin_close REAL,
                recorded_on TEXT,
                bands TEXT,
                research_context TEXT,
                UNIQUE(symbol, origin, engine, predicted)
            )
        """)

        columns = {row[1] for row in connection.execute("PRAGMA table_info(forecast_runs)")}
        for column, definition in (("origin_close", "REAL"), ("recorded_on", "TEXT"), ("bands", "TEXT"),
                                   ("research_context", "TEXT")):
            if column not in columns:
                connection.execute(f"ALTER TABLE forecast_runs ADD COLUMN {column} {definition}")

    def save(self, symbol, origin, engine, path, *, origin_close=None, bands=None, recorded_on=None, research_context=None):
        recorded_on = recorded_on or datetime.now(timezone.utc).date()
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO forecast_runs(symbol, origin, engine, predicted, origin_close, recorded_on, bands, research_context) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, origin.isoformat(), engine, json.dumps(list(path), allow_nan=False),
                 origin_close, recorded_on.isoformat(), json.dumps(bands, allow_nan=False), json.dumps(research_context, allow_nan=False)),
            )

    def calibrate(self, symbol, cutoff, origin_close, path):
        cutoff = cutoff.isoformat()
        rows = self.connection.execute(
            "SELECT origin, predicted, observed, origin_close, recorded_on FROM forecast_runs "
            "WHERE symbol = ? AND engine = 'llama.cpp' AND origin < ? AND origin_close > 0 "
            "AND recorded_on IS NOT NULL ORDER BY origin DESC, id ASC LIMIT 1000",
            (symbol, cutoff),
        )
        history = [{} for _ in path]
        for origin, predicted, observed, close, recorded in rows:
            for step, (estimate, (day, actual)) in enumerate(zip(json.loads(predicted), json.loads(observed))):
                if step >= len(path):
                    break
                if recorded < day <= cutoff:
                    history[step].setdefault(origin, (day, abs(actual - estimate) / close))

        bands = []
        for estimate, observations in zip(path, history):
            if len(observations) < MIN_CALIBRATION:
                bands.append(None)
                continue
            available, checked, covered = [], 0, 0
            for origin, (day, error) in sorted(observations.items()):
                known = [value for outcome, value in available if outcome <= origin]
                if len(known) >= MIN_CALIBRATION:
                    checked += 1
                    covered += error <= _radius(known)
                available.append((day, error))
            radius = _radius([error for _, error in observations.values()]) * origin_close
            bands.append(CalibratedBand(max(0., estimate - radius), estimate + radius,
                                        len(observations), covered, checked))
        return tuple(bands)

    def watch_rows(self, symbols):
        for symbol in symbols:
            rows = self.connection.execute(
                "SELECT origin, engine, predicted, observed, bands FROM forecast_runs WHERE symbol = ? "
                "ORDER BY origin DESC, id DESC LIMIT 100", (symbol,),
            )
            pending = completed = outside = checked = 0
            latest = None
            for origin, engine, predicted, observed, bands in rows:
                path, actual = json.loads(predicted), json.loads(observed)
                bounds = json.loads(bands) if bands else None
                if latest is None:
                    latest = (origin, engine, len(path), path[-1], actual[-1][1] if len(actual) >= len(path) else None)
                pending += len(actual) < len(path)
                completed += len(actual) >= len(path)
                for value, interval in zip(actual, bounds or ()):
                    if interval is not None:
                        checked += 1
                        outside += not interval[0] <= value[1] <= interval[1]
            yield symbol, latest, pending, completed, outside, checked

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
