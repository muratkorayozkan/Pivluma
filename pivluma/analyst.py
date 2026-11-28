"""Explain the active analysis using locally computed values."""

import re
from statistics import mean, pstdev
from typing import Sequence

from pivluma.data import Price
from pivluma.forecast import Analysis, NEWS_MODEL, TRAINING_WINDOW
from pivluma.localization import translate


def price_statistics(prices):
    closes = [price.close for price in prices]
    returns = [(current / previous - 1) * 100 for previous, current in zip(closes, closes[1:])]
    peak, drawdown = closes[0], 0.
    for close in closes:
        peak = max(peak, close)
        drawdown = max(drawdown, (1 - close / peak) * 100)
    rsi = None
    if len(closes) >= 15:
        moves = [current - previous for previous, current in zip(closes[-15:-1], closes[-14:])]
        gains = sum(max(0., move) for move in moves)
        losses = sum(max(0., -move) for move in moves)
        rsi = 100 * gains / (gains + losses) if gains or losses else 50.
    low = min(prices, key=lambda price: price.close)
    high = max(prices, key=lambda price: price.close)
    return {
        "volatility_percent_per_observation": pstdev(returns) if returns else 0.,
        "max_drawdown_percent": drawdown,
        "low_close": low.close, "low_date": str(low.day),
        "high_close": high.close, "high_date": str(high.day),
        "rsi_14_simple": rsi,
    }


def answer_statistics(question, context, prices, language):
    words = set(re.findall(r"[^\W\d_]+", question.casefold()))
    query = question.casefold()
    if words & {"volatility", "volatilite", "oynaklık", "oynaklığı"} or "波动率" in query:
        template = "Close-to-close volatility is {volatility_percent_per_observation:.2f}% per observation."
    elif words & {"drawdown", "gerileme", "gerilemesi"} or "maksimum düşüş" in query or "最大回撤" in query:
        template = "Maximum peak-to-trough drawdown was {max_drawdown_percent:.2f}%."
    elif (words & {"highest", "lowest", "zirve", "dip"} or "en yüksek" in query
          or "en düşük" in query or "最高" in query or "最低" in query):
        template = "Lowest close: {low_close:.8g} ({low_date}). Highest close: {high_close:.8g} ({high_date})."
    elif "rsi" in query or "相对强弱" in query or "göreceli güç" in query:
        template = ("RSI needs at least 15 closing prices." if len(prices) < 15 else
                    "The 14-observation RSI (simple averages) is {rsi_14_simple:.1f} out of 100.")
    else:
        return None
    return f"{context}\n" + translate(language, template).format(**price_statistics(prices))


def answer(question: str, label: str, prices: Sequence[Price], result: Analysis, language: str = "en") -> str:
    if language == "zh":
        return answer_chinese(question, label, prices, result)
    words = set(re.findall(r"[^\W\d_]+", question.casefold()))
    first, latest = prices[0], prices[-1]
    turkish = language == "tr"
    period = "–" if turkish else "to"
    context = f"{label} | {first.day} {period} {latest.day}"
    if detail := answer_statistics(question, context, prices, language):
        return detail
    model = translate(language, result.model)
    if words & {"news", "headlines", "sentiment", "haber", "haberler", "duygu"}:
        if turkish:
            detail = ("Son tahminde araştırma modeli seçildi." if result.model == NEWS_MODEL
                      else "Son tahminde fiyat modeli seçildi.")
            return f"{context}\nAraştırma sinyali ve sonraki getiri için {result.news_pairs} eşleşme var. {detail}"
        detail = ("The research model was selected for the current forecast." if result.model == NEWS_MODEL
                  else "A price-only model was selected for the current forecast.")
        return f"{context}\nThere are {result.news_pairs} matched research-signal and subsequent-return pairs. {detail}"
    if "ortalama" in words or {"moving", "average"} <= words or "sma" in words:
        closes = [price.close for price in prices[-TRAINING_WINDOW:]]
        average = mean(closes)
        if turkish:
            return (f"{context}\nSon {len(closes)} gözlem için hareketli ortalama {average:.2f}. "
                    f"Son kapanış {latest.close:.2f}, ortalamadan fark {latest.close - average:+.2f}.")
        return (f"{context}\nThe {len(closes)}-observation moving average is {average:.2f}. "
                f"The last close was {latest.close:.2f}, {latest.close - average:+.2f} versus the average.")
    if words & {"backtest", "accuracy", "accurate", "performance", "mae", "baseline", "error", "doğruluk", "performans",
                "hata", "başarılı", "başarısı", "test"}:
        comparison = "Both had the same MAE."
        if result.mae < result.baseline_mae:
            comparison = "The adaptive strategy had the lower MAE."
        elif result.mae > result.baseline_mae:
            comparison = "The last-price baseline had the lower MAE."
        gap = abs(result.mae - result.baseline_mae)
        if turkish:
            comparison = "İki modelin MAE değeri eşit."
            if result.mae < result.baseline_mae:
                comparison = "Uyarlamalı stratejinin MAE değeri daha düşük."
            elif result.mae > result.baseline_mae:
                comparison = "Son fiyatı sabit tutan referansın MAE değeri daha düşük."
            return (
                f"{context}\n{result.origins} geçmiş test başlangıcında ortalama mutlak hata "
                f"uyarlamalı strateji için {result.mae:.2f}, referans için {result.baseline_mae:.2f}. "
                f"Fark {gap:.2f}. {comparison}"
            )
        return (
            f"{context}\nAcross {result.origins} walk-forward test origins, mean absolute "
            f"error was {result.mae:.2f} for the adaptive strategy and {result.baseline_mae:.2f} "
            f"for the baseline. The difference was {gap:.2f}. {comparison}"
        )

    if words & {"forecast", "forecasting", "prediction", "predict", "interval", "range", "outlook",
                "tahmin", "tahmini", "aralık", "aralığı", "öngörü"}:
        if turkish:
            return (
                f"{context}\n{model} modeli {len(result.forecast)} gözlem sonrası için "
                f"{result.forecast[-1]:.2f} öngörüyor. Son kapanış {latest.close:.2f}. "
                f"Geçmiş hatalara dayalı aralık [{result.lower[-1]:.2f}, {result.upper[-1]:.2f}]. "
                f"Model, geçmiş tahmin hatalarına göre seçildi."
            )
        return (
            f"{context}\nThe {model} model estimates {result.forecast[-1]:.2f} at "
            f"{len(result.forecast)} observations ahead, from a last close of {latest.close:.2f}. "
            f"The empirical interval at that step is [{result.lower[-1]:.2f}, "
            f"{result.upper[-1]:.2f}], based on past forecast errors. The model was selected "
            f"using completed historical forecasts."
        )

    if words & {"price", "prices", "move", "movement", "change", "history", "close", "fiyat", "fiyatı", "hareket",
                "değişti", "değişim", "kapanış"}:
        change = (latest.close / first.close - 1) * 100
        if turkish:
            return (
                f"{context}\n{len(prices)} gözlem boyunca kapanış fiyatı "
                f"{first.close:.2f} seviyesinden {latest.close:.2f} seviyesine geldi "
                f"(%{change:+.2f})."
            )
        return (
            f"{context}\nThe closing price went from {first.close:.2f} to "
            f"{latest.close:.2f} ({change:+.2f}%) across {len(prices)} observations."
        )

    if turkish:
        return "Fiyat hareketi, tahmin, oynaklık, azami gerileme, en yüksek ve düşük kapanışlar, RSI, hareketli ortalama veya geçmiş test hakkında soru sorabilirsiniz."
    return "Ask about one of the supported topics: price movement, forecast, volatility, drawdown, closing extremes, RSI, moving average, or backtest performance."



def answer_chinese(question, label, prices, result):
    first, latest = prices[0], prices[-1]
    period = f"{label} | {first.day} 至 {latest.day}"
    if detail := answer_statistics(question, period, prices, "zh"):
        return detail
    question = question.casefold()
    if any(term in question for term in ("新闻", "情绪", "研究", "news")):
        mode = "研究信号模型" if result.model == NEWS_MODEL else "价格模型"
        return f"{period}\n研究信号与后续收益共匹配 {result.news_pairs} 组。当前预测采用{mode}。"
    if any(term in question for term in ("移动平均", "均线", "moving average", "sma")):
        values = [price.close for price in prices[-TRAINING_WINDOW:]]
        average = mean(values)
        return (f"{period}\n最近 {len(values)} 个收盘价的移动平均为 {average:.2f}。"
                f"最新收盘价为 {latest.close:.2f}，与均值相差 {latest.close - average:+.2f}。")
    if any(term in question for term in ("回测", "准确", "误差", "backtest", "mae")):
        return (f"{period}\n在 {result.origins} 个回测起点上，自适应策略的 MAE 为 {result.mae:.2f}，"
                f"最近价格基准为 {result.baseline_mae:.2f}。")
    if any(term in question for term in ("预测", "区间", "forecast")):
        model = translate("zh", result.model)
        return (f"{period}\n{model}预测未来第 {len(result.forecast)} 步为 {result.forecast[-1]:.2f}，"
                f"经验区间为 [{result.lower[-1]:.2f}, {result.upper[-1]:.2f}]。")
    if any(term in question for term in ("价格", "收盘", "变动", "price")):
        move = (latest.close / first.close - 1) * 100
        return (f"{period}\n{len(prices)} 个观察值中，收盘价从 {first.close:.2f} "
                f"变为 {latest.close:.2f}（{move:+.2f}%）。")
    return "可以询问价格变动、预测、波动率、最大回撤、最高和最低收盘价、RSI、移动平均、回测或研究信号。"
