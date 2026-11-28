"""Explain the active analysis using locally computed values."""

import re
from statistics import mean
from typing import Sequence

from pivluma.data import Price
from pivluma.forecast import Analysis, NEWS_MODEL, TRAINING_WINDOW
from pivluma.localization import translate


def answer(question: str, label: str, prices: Sequence[Price], result: Analysis, language: str = "en") -> str:
    if language == "zh":
        return answer_chinese(question, label, prices, result)
    words = set(re.findall(r"[^\W\d_]+", question.casefold()))
    first, latest = prices[0], prices[-1]
    turkish = language == "tr"
    period = "–" if turkish else "to"
    context = f"{label} | {first.day} {period} {latest.day}"
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
        return "Fiyat hareketi, tahmin veya hareketli ortalama veya geçmiş test performansı hakkında soru sorabilirsiniz."
    return "Ask about one of the supported topics: price movement, forecast, moving average, or backtest performance."



def answer_chinese(question, label, prices, result):
    first, latest = prices[0], prices[-1]
    period = f"{label} | {first.day} 至 {latest.day}"
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
    return "可以询问价格变动、预测、移动平均、回测或研究信号。"
