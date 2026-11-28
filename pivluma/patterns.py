"""Find comparable price regimes with fully observed subsequent returns."""

from datetime import timedelta
from math import sqrt
from statistics import mean, median, pstdev


def _features(prices, scores, index):
    window = prices[index - 20:index + 1]
    close = window[-1].close
    changes = [(right.close / left.close - 1) * 100 for left, right in zip(window, window[1:])]
    reference_day = window[-1].day
    tones = [scores[reference_day - timedelta(days=days)] for days in range(1, 8)
             if reference_day - timedelta(days=days) in scores]
    return (
        (close / window[-6].close - 1) * 100,
        (close / window[0].close - 1) * 100,
        pstdev(changes),
        mean(tones) * 100 if tones else None,
    )


def historical_patterns(prices, scores, horizon):
    latest = len(prices) - 1
    if latest < 20 + horizon:
        return {"matches": 0}
    current = _features(prices, scores, latest)
    features = [(index, _features(prices, scores, index))
                for index in range(max(20, latest - 260), latest - horizon + 1)]
    use_tone = current[3] is not None and sum(row[3] is not None for _, row in features) >= 8
    if use_tone:
        features = [(index, row) for index, row in features if row[3] is not None]
    dims = 4 if use_tone else 3
    scales = [max(.15, pstdev(row[column] for _, row in features)) for column in range(dims)]
    ranked = sorted((sqrt(mean(((row[column] - current[column]) / scales[column]) ** 2
                                for column in range(dims))), index) for index, row in features)
    chosen = []
    spacing = min(horizon, 5)
    for distance, index in ranked:
        if distance > 2.5:
            break
        if all(abs(index - other) >= spacing for _, other in chosen):
            chosen.append((distance, index))
        if len(chosen) >= 12:
            break
    if len(chosen) < 3:
        return {"matches": 0}
    returns = [(prices[index + horizon].close / prices[index].close - 1) * 100 for _, index in chosen]
    return {
        "matches": len(chosen), "horizon": horizon, "matched_on_research_tone": use_tone,
        "median_return_percent": round(median(returns), 3),
        "positive_outcome_share_percent": round(100 * sum(value > 0 for value in returns) / len(returns), 1),
        "minimum_return_percent": round(min(returns), 3), "maximum_return_percent": round(max(returns), 3),
        "median_distance": round(median(distance for distance, _ in chosen), 2),
    }
