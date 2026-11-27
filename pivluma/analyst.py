"""Explain the active analysis using locally computed values."""

import re
from statistics import mean
from typing import Sequence

from pivluma.data import Price
from pivluma.forecast import Analysis, TRAINING_WINDOW
from pivluma.localization import translate


def answer(question: str, label: str, prices: Sequence[Price], result: Analysis, language: str = "en") -> str:
    words = set(re.findall(r"[^\W\d_]+", question.casefold()))
    first, latest = prices[0], prices[-1]
    turkish = language == "tr"
    period = "–" if turkish else "to"
    context = f"{label} | {first.day} {period} {latest.day}"
    model = translate(language, result.model)
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
