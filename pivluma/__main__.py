"""Run the offline forecasting workflow from the command line."""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from pivluma.data import PriceStore, read_prices, sample_prices
from pivluma.forecast import analyze


def main() -> None:
    parser = argparse.ArgumentParser(description="Pivluma offline market analysis")
    parser.add_argument("--horizon", type=int, default=5, help="Number of future observations")
    parser.add_argument("--database", type=Path, default=Path("pivluma.sqlite3"))
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--import-csv", type=Path, metavar="FILE")
    source.add_argument("--load", action="store_true", help="Analyze a stored series")
    parser.add_argument("--symbol", help="Series identifier for import or load")
    args = parser.parse_args()
    if (args.import_csv or args.load) and not (args.symbol or "").strip():
        parser.error("--symbol is required for import or load")

    try:
        if args.import_csv:
            with args.import_csv.open(encoding="utf-8-sig", newline="") as stream:
                prices = read_prices(stream)
        elif args.load:
            with closing(sqlite3.connect(args.database)) as connection:
                prices = PriceStore(connection).load(args.symbol)
            if not prices:
                raise ValueError(f"No stored prices for {args.symbol}.")
        else:
            prices = sample_prices()

        result = analyze([price.close for price in prices], args.horizon)
        if args.import_csv:
            with closing(sqlite3.connect(args.database)) as connection:
                PriceStore(connection).replace(args.symbol, prices)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(2, f"Error: {error}\n")

    label = args.symbol if args.load or args.import_csv else "Synthetic sample"
    print(f"{label} | {prices[0].day} to {prices[-1].day} | {len(prices)} observations")
    print(f"Walk-forward MAE: {result.mae:.2f} | Last-price MAE: {result.baseline_mae:.2f}"
          f" | Origins: {result.origins}")
    print("Step    Forecast       Empirical interval")
    for step, (value, lower, upper) in enumerate(zip(result.forecast, result.lower, result.upper), 1):
        print(f"{step:>4}    {value:>8.2f}       [{lower:.2f}, {upper:.2f}]")


if __name__ == "__main__":
    main()
