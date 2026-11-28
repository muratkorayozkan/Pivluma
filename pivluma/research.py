"""Causal summaries of price reactions following research scores."""

from datetime import timedelta
from math import sqrt
from statistics import mean

from pivluma.localization import translate


def research_evidence(prices, scores, source):
    latest = prices[-1].day
    recent = [(day, score) for day, score in scores.items()
              if latest - timedelta(days=30) <= day < latest]
    recent.sort()
    pairs = []
    window = prices[-91:]
    for current, following in zip(window, window[1:]):
        score = scores.get(current.day - timedelta(days=1))
        if score is not None:
            pairs.append((score, (following.close / current.close - 1) * 100))
    positive = [change for score, change in pairs if score >= .15]
    negative = [change for score, change in pairs if score <= -.15]
    current_tone = mean(score for _, score in recent[-7:]) if recent else None
    correlation = beta = None
    if len(pairs) >= 20 and len(positive) >= 5 and len(negative) >= 5:
        center_score = mean(score for score, _ in pairs)
        center_return = mean(change for _, change in pairs)
        covariance = sum((score - center_score) * (change - center_return) for score, change in pairs)
        spread_score = sum((score - center_score) ** 2 for score, _ in pairs)
        spread_return = sum((change - center_return) ** 2 for _, change in pairs)
        if spread_score and spread_return:
            correlation = covariance / sqrt(spread_score * spread_return)
            beta = covariance / (spread_score + 2.)
    influence = 0.
    if current_tone is not None and correlation is not None and abs(correlation) >= .15:
        influence = max(-.5, min(.5, beta * current_tone))
    return {
        "source": source if recent or pairs else "",
        "recent_tone": round(current_tone * 100, 1) if current_tone is not None else None,
        "matched_returns": len(pairs),
        "positive_count": len(positive), "negative_count": len(negative),
        "after_positive_percent": round(mean(positive), 3) if positive else None,
        "after_negative_percent": round(mean(negative), 3) if negative else None,
        "historical_correlation": round(correlation, 3) if correlation is not None else None,
        "bounded_adjustment_percent": round(influence, 4),
    }


def summarize_research(evidence, language, patterns=None):
    def line(message):
        return translate(language, message).format(**evidence)

    if not evidence["source"]:
        return line("No research scores are available for this series. Use Research news or import scores.")
    details = [line("{source} · {matched_returns} lagged price reactions.")]
    if evidence["recent_tone"] is not None:
        details.append(line("Mean of recent normalized scores: {recent_tone:+.1f} on a −100 to +100 scale."))
    if evidence["after_positive_percent"] is not None and evidence["after_negative_percent"] is not None:
        details.append(line("Mean subsequent return after positive scores: {after_positive_percent:+.2f}% "
                            "({positive_count} cases). After negative scores: {after_negative_percent:+.2f}% "
                            "({negative_count} cases)."))
    if not evidence["bounded_adjustment_percent"]:
        details.append(line("The data do not support a directional research adjustment."))
    else:
        details.append(line("Historical correlation: {historical_correlation:+.2f}. "
                            "Estimated short-term adjustment: {bounded_adjustment_percent:+.2f} percentage points."))
    if patterns and patterns["matches"]:
        message = ("{matches} similar past periods: median {horizon}-step return {median_return_percent:+.2f}%, "
                   "with outcomes from {minimum_return_percent:+.2f}% to {maximum_return_percent:+.2f}%.")
        details.append(translate(language, message).format(**patterns))
        match_mode = ("Past periods matched on price and research tone." if patterns["matched_on_research_tone"]
                      else "Past periods matched on price features.")
        details.append(line(match_mode))
    details.append(line("These are aggregate scores and historical associations. Specific events cannot be identified from them."))
    return "\n".join(details)
