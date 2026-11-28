"""Causal model selection and walk-forward evaluation for closing prices."""

from collections import deque
from dataclasses import dataclass
from math import exp, isfinite, log
from statistics import linear_regression, mean, median
from typing import Sequence


@dataclass(frozen=True)
class Analysis:
    forecast: tuple[float, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    mae: float
    baseline_mae: float
    origins: int
    model: str = "adaptive"
    # (lead, number of observations used when choosing the final model)
    validation: tuple[tuple[int, int], ...] = ()
    news_pairs: int = 0


TRAINING_WINDOW = 20
MIN_TRAINING = 8
SCORE_WINDOW = 32
NEWS_MODEL = "Research-adjusted baseline"
MIN_NEWS_PAIRS = 20
MODEL_NAMES = ("Last-price baseline", "Linear trend", "Damped trend", "Smoothed level", "Robust return trend")


def _paths(prices: Sequence[float], horizon: int):
    slope, intercept = linear_regression(range(len(prices)), prices)
    latest = prices[-1]
    level = prices[0]
    for price in prices[1:]:
        level += .3 * (price - level)
    recent = [right - left for left, right in zip(prices[-8:], prices[-7:])]
    drift = mean(recent)
    logs = [log(price) for price in prices]
    # Median returns reduce the influence of isolated price jumps.
    returns = [right - left for left, right in zip(logs, logs[1:])]
    log_drift = median(returns)
    deviation = median(abs(value - log_drift) for value in returns)
    if deviation > abs(log_drift) / 2:
        log_drift = 0.
    log_drift = max(-.10, min(.10, log_drift))
    return (
        (latest,) * horizon,
        tuple(max(0., intercept + slope * (len(prices) + step)) for step in range(horizon)),
        tuple(max(0., latest + drift * (1 - .88 ** step) / .12) for step in range(1, horizon + 1)),
        (level,) * horizon,
        tuple(latest * exp(log_drift * step) for step in range(1, horizon + 1)),
    )


def _news_path(latest, tone, pairs, horizon):
    if tone is None or len(pairs) < MIN_NEWS_PAIRS:
        return (latest,) * horizon
    # Ridge shrinkage reduces sensitivity to a handful of extreme news days.
    gradient = sum(signal * response for signal, response in pairs) / (
        2.0 + sum(signal * signal for signal, _ in pairs)
    )
    shock = max(-.02, min(.02, gradient * tone))
    return tuple(latest * (1 + shock * .85 ** step) for step in range(horizon))


def analyze(prices: Sequence[float], horizon: int, news: Sequence[float | None] | None = None) -> Analysis:
    """Choose each origin's model using only already-observed forecast errors."""
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
        raise ValueError("Horizon must be a positive integer.")
    if len(prices) < TRAINING_WINDOW + horizon + 9:
        raise ValueError(f"Provide at least {TRAINING_WINDOW + horizon + 9} prices.")
    if any(not isfinite(price) or price <= 0 for price in prices):
        raise ValueError("Prices must be finite and positive.")

    if news is not None and (len(news) != len(prices) or any(
        value is not None and (not isfinite(value) or abs(value) > 1) for value in news
    )):
        raise ValueError("Invalid aligned news scores.")
    names = MODEL_NAMES + (NEWS_MODEL,) if news is not None else MODEL_NAMES
    news_pairs = deque(maxlen=90)

    leads = sorted({1, max(1, horizon // 4), max(1, horizon // 2), horizon})
    scores = [[deque(maxlen=SCORE_WINDOW) for _ in leads] for _ in names]
    pending = {}
    errors = [[] for _ in range(horizon)]
    baseline_errors = []
    final_origin = len(prices)
    for origin in range(MIN_TRAINING, final_origin + 1):
        realized = prices[origin - 1]
        if news is not None and origin == MIN_TRAINING:
            for index in range(origin - 2):
                if news[index] is not None:
                    news_pairs.append((news[index], prices[index + 1] / prices[index] - 1))
        if news is not None and news[origin - 2] is not None:
            news_pairs.append((news[origin - 2], realized / prices[origin - 2] - 1))
        for lead_index, lead in enumerate(leads):
            matured = pending.get(origin - lead)
            if matured is not None:
                for model, forecast in enumerate(matured):
                    scores[model][lead_index].append(abs(realized - forecast[lead - 1]))
        pending.pop(origin - horizon, None)

        paths = _paths(prices[max(0, origin - TRAINING_WINDOW):origin], horizon)
        if news is not None:
            paths += (_news_path(realized, news[origin - 1], news_pairs, horizon),)
        pending[origin] = paths
        if origin < TRAINING_WINDOW:
            continue

        # Give equal weight to evaluated lead times. Ties prefer the baseline.
        def score(model):
            samples = [mean(history) for history in scores[model] if len(history) >= 3]
            return mean(samples) if samples else 0.

        available = len(names)
        if news is not None and (news[origin - 1] is None or len(news_pairs) < MIN_NEWS_PAIRS):
            available -= 1
        eligible = (index for index in range(available)
                    if names[index] != "Robust return trend" or paths[index] != paths[0])
        selected = min(eligible, key=score)
        if origin <= final_origin - horizon:
            actual = prices[origin:origin + horizon]
            for step, (prediction, observed) in enumerate(zip(paths[selected], actual)):
                errors[step].append(abs(observed - prediction))
                baseline_errors.append(abs(observed - paths[0][step]))

    forecast = paths[selected]
    radii = tuple(sorted(step_errors)[(9 * len(step_errors) + 9) // 10 - 1]
                  for step_errors in errors)
    return Analysis(
        forecast=forecast,
        lower=tuple(max(0.0, price - radius) for price, radius in zip(forecast, radii)),
        upper=tuple(price + radius for price, radius in zip(forecast, radii)),
        mae=mean(error for step_errors in errors for error in step_errors),
        baseline_mae=mean(baseline_errors),
        origins=len(errors[0]),
        model=names[selected],
        news_pairs=len(news_pairs),
        validation=tuple((lead, len(scores[selected][index])) for index, lead in enumerate(leads)),
    )
