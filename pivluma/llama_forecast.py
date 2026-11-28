"""Constrained local-model forecast request and chart path reconstruction."""
import json
from datetime import timedelta
from math import isfinite
from statistics import median, pstdev

from pivluma.patterns import historical_patterns
from pivluma.research import research_evidence

FORECAST_TASK = "llama-forecast"


def anchor_steps(horizon):
    return sorted({1, horizon, *(max(1, round(horizon * fraction)) for fraction in (.25, .5, .75))})


def forecast_input(prices, horizon, scores, source):
    latest = prices[-1]
    recent = prices[-42:]
    news = sorted((day, score) for day, score in scores.items()
                  if latest.day - timedelta(days=30) <= day < latest.day)[-14:]
    returns = [(right.close / left.close - 1) * 100 for left, right in zip(recent, recent[1:])]
    return {
        "last_close": latest.close,
        "as_of": str(latest.day),
        "price_context_percent": {
            "last_5_observations": round((latest.close / recent[-6].close - 1) * 100, 4),
            "full_recent_window": round((latest.close / recent[0].close - 1) * 100, 4),
            "median_daily_return": round(median(returns), 4),
            "daily_volatility": round(pstdev(returns), 4),
        },
        "recent_returns_percent": [round(value, 4) for value in returns],
        "requested_steps": anchor_steps(horizon),
        "research_source": source if news else "",
        "research_evidence": research_evidence(prices, scores, source),
        "historical_patterns": historical_patterns(prices, scores, horizon),
        "research_scores": [{"day": str(day), "score": round(score * 100, 2)} for day, score in news],
    }


def forecast_request(facts):
    length = len(facts["requested_steps"])
    return {
        "model": "llama", "stream": False, "temperature": 0, "max_tokens": 160,
        "response_format": {"type": "json_schema", "json_schema": {"schema": {
            "type": "object", "properties": {"changes": {
                "type": "array", "items": {"type": "number"}, "minItems": length, "maxItems": length,
            }}, "required": ["changes"], "additionalProperties": False,
        }}},
        "messages": [
            {"role": "system", "content": (
                "Predict cumulative relative percentage returns. Use historical returns as the main evidence. Compare the five-observation move, median return, and volatility before extrapolating. "
                "Check research_evidence for the number of observations, differences in subsequent returns, "
                "and correlation. Treat scores as weak evidence and do not infer specific events. "
                "Compare the current regime with historical_patterns. Its median is a reference for the last "
                "requested step. Wide ranges or few matches weaken that evidence. "
                "When price history and research disagree, reduce the size of the move. "
                "Estimate a return for every requested step in order. Step numbers are time indices, "
                "never copy them into the result. Each output is a signed percentage from the latest close. "
                "Return only the JSON with your estimates."
            )},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
        ],
    }


def forecast_path(response, last_close, horizon, research_adjustment=0., patterns=None):
    steps = anchor_steps(horizon)
    payload = json.loads(response)
    changes = payload.get("changes") if isinstance(payload, dict) else None
    if not isinstance(changes, list) or len(changes) != len(steps):
        raise ValueError("Unexpected forecast shape")
    if any(type(change) not in (int, float) or not isfinite(change) or not -100 < change <= 500
           for change in changes):
        raise ValueError("Invalid forecast change")
    if changes == steps:
        raise ValueError("Model echoed the time indices")
    pattern_weight = 0.
    if patterns and patterns["matches"] >= 8 and patterns["median_distance"] <= 1:
        positive_share = patterns["positive_outcome_share_percent"]
        if positive_share >= 65 or positive_share <= 35:
            pattern_weight = .2
    path = []
    previous_step, previous = 0, last_close
    for step, change in zip(steps, changes):
        if pattern_weight:
            reference = patterns["median_return_percent"] * step / horizon
            change += pattern_weight * (reference - change)
        target = last_close * (1 + (change + research_adjustment * .95 ** (step - 1)) / 100)
        path.extend(previous + (target - previous) * offset / (step - previous_step)
                    for offset in range(1, step - previous_step + 1))
        previous_step, previous = step, target
    if any(not isfinite(price) or price <= 0 for price in path):
        raise ValueError("Invalid forecast price")
    return tuple(path)
