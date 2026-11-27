"""Explain the active analysis using locally computed values."""

import re
from typing import Sequence

from pivluma.data import Price
from pivluma.forecast import Analysis, TRAINING_WINDOW


def answer(question: str, label: str, prices: Sequence[Price], result: Analysis) -> str:
    words = set(re.findall(r"[a-z]+", question.lower()))
    first, latest = prices[0], prices[-1]
    context = f"{label} | {first.day} to {latest.day}"
    if words & {"backtest", "accuracy", "accurate", "performance", "mae", "baseline", "error"}:
        comparison = "Both had the same MAE."
        if result.mae < result.baseline_mae:
            comparison = "The trend model had the lower MAE."
        elif result.mae > result.baseline_mae:
            comparison = "The last-price baseline had the lower MAE."
        return (
            f"{context}\nAcross {result.origins} walk-forward test origins, mean absolute "
            f"error was {result.mae:.2f} for the trend model and {result.baseline_mae:.2f} "
            f"for the baseline. {comparison}"
        )

    if words & {"forecast", "forecasting", "prediction", "predict", "interval", "range", "outlook"}:
        return (
            f"{context}\nThe trend model estimates {result.forecast[-1]:.2f} at "
            f"{len(result.forecast)} observations ahead, from a last close of {latest.close:.2f}. "
            f"The empirical interval at that step is [{result.lower[-1]:.2f}, "
            f"{result.upper[-1]:.2f}], based on past forecast errors. The trend is fitted "
            f"to the latest {TRAINING_WINDOW} observations."
        )

    if words & {"price", "prices", "move", "movement", "change", "history", "close"}:
        change = (latest.close / first.close - 1) * 100
        return (
            f"{context}\nThe closing price went from {first.close:.2f} to "
            f"{latest.close:.2f} ({change:+.2f}%) across {len(prices)} observations."
        )

    return "Ask about one of the supported topics: price movement, forecast, or backtest performance."
