"""Small, deterministic forecasting models for regularly ordered observations."""

from dataclasses import dataclass
from math import isfinite
from statistics import linear_regression, mean
from typing import Sequence


@dataclass(frozen=True)
class Analysis:
    forecast: tuple[float, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    mae: float
    baseline_mae: float
    origins: int


TRAINING_WINDOW = 20


def _trend(prices: Sequence[float], horizon: int) -> tuple[float, ...]:
    slope, intercept = linear_regression(range(len(prices)), prices)
    return tuple(max(0.0, intercept + slope * (len(prices) + step))
                 for step in range(horizon))


def analyze(prices: Sequence[float], horizon: int) -> Analysis:
    """Fit a rolling linear trend and evaluate it at earlier forecast origins."""
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
        raise ValueError("Horizon must be a positive integer.")
    if len(prices) < TRAINING_WINDOW + horizon + 9:
        raise ValueError(f"Provide at least {TRAINING_WINDOW + horizon + 9} prices.")
    if any(not isfinite(price) or price <= 0 for price in prices):
        raise ValueError("Prices must be finite and positive.")

    errors = [[] for _ in range(horizon)]
    baseline_errors = []
    for origin in range(TRAINING_WINDOW, len(prices) - horizon + 1):
        training = prices[origin - TRAINING_WINDOW:origin]
        predictions = _trend(training, horizon)
        for step, (prediction, actual) in enumerate(zip(predictions, prices[origin:origin + horizon])):
            errors[step].append(abs(actual - prediction))
            baseline_errors.append(abs(actual - training[-1]))

    forecast = _trend(prices[-TRAINING_WINDOW:], horizon)
    radii = tuple(sorted(step_errors)[(9 * len(step_errors) + 9) // 10 - 1]
                  for step_errors in errors)
    return Analysis(
        forecast=forecast,
        lower=tuple(max(0.0, price - radius) for price, radius in zip(forecast, radii)),
        upper=tuple(price + radius for price, radius in zip(forecast, radii)),
        mae=mean(error for step_errors in errors for error in step_errors),
        baseline_mae=mean(baseline_errors),
        origins=len(errors[0]),
    )
