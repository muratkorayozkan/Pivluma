"""Daily closing prices for offline analysis."""

import csv
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite, sin
from typing import Iterable, TextIO


@dataclass(frozen=True)
class Price:
    day: date
    close: float

    def __post_init__(self) -> None:
        if type(self.day) is not date:
            raise ValueError("Expected a calendar date.")
        if not isfinite(self.close) or self.close <= 0:
            raise ValueError("Closing prices must be finite and positive.")


def read_prices(stream: TextIO) -> list[Price]:
    """Read UTF-8 CSV data into a chronological series."""
    reader = csv.reader(stream, strict=True)
    prices = {}
    try:
        header = next(reader, [])
        if header:
            header[0] = header[0].lstrip("\ufeff")
        header = [column.strip().lower() for column in header]
        if header.count("date") != 1 or header.count("close") != 1:
            raise ValueError("CSV requires one date column and one close column.")
        date_column, close_column = header.index("date"), header.index("close")
        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            try:
                if len(row) != len(header):
                    raise ValueError("Column count differs from the header.")
                raw_day = row[date_column].strip()
                day = date.fromisoformat(raw_day)
                if raw_day != day.isoformat():
                    raise ValueError("Use YYYY-MM-DD dates.")
                if day in prices:
                    raise ValueError(f"Duplicate date {day}.")
                prices[day] = Price(day, float(row[close_column]))
            except ValueError as error:
                raise ValueError(f"Line {reader.line_num}: {error}") from error
    except csv.Error as error:
        raise ValueError(f"Line {reader.line_num}: {error}") from error
    if not prices:
        raise ValueError("CSV contains no price observations.")
    return [prices[day] for day in sorted(prices)]


def sample_prices() -> list[Price]:
    """Return deterministic synthetic prices with a trend and oscillation."""
    start = date(2025, 1, 1)
    return [Price(start + timedelta(days=day), round(100 + day * .18 + 3 * sin(day / 5), 2))
            for day in range(120)]


class PriceStore:
    """Store independently replaceable daily series keyed by symbol."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS daily_prices (
                symbol TEXT NOT NULL,
                day TEXT NOT NULL,
                close REAL NOT NULL CHECK (close > 0),
                PRIMARY KEY (symbol, day)
            )
        """)

    def replace(self, symbol: str, prices: Iterable[Price]) -> None:
        symbol = symbol.strip()
        rows = [(symbol, price.day.isoformat(), price.close) for price in prices]
        if not symbol or not rows:
            raise ValueError("A symbol and at least one price are required.")
        if len({day for _, day, _ in rows}) != len(rows):
            raise ValueError("A price series cannot contain duplicate dates.")
        with self.connection:
            self.connection.execute("DELETE FROM daily_prices WHERE symbol = ?", (symbol,))
            self.connection.executemany(
                "INSERT INTO daily_prices (symbol, day, close) VALUES (?, ?, ?)", rows
            )

    def load(self, symbol: str) -> list[Price]:
        rows = self.connection.execute(
            "SELECT day, close FROM daily_prices WHERE symbol = ? ORDER BY day", (symbol.strip(),)
        )
        return [Price(date.fromisoformat(day), close) for day, close in rows]
