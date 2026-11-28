"""Map the active instrument to a public TradingView chart link."""
import re
from urllib.parse import urlencode

from pivluma.market import FX_SYMBOLS, PUBLIC_SYMBOLS

LISTINGS = {
    **dict.fromkeys(("AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD", "NFLX", "QQQ"), "NASDAQ"),
    **dict.fromkeys(("JPM", "V", "WMT"), "NYSE"),
    "SPY": "AMEX",
    "DIA": "AMEX",
}


def tradingview_url(symbol):
    symbol = symbol.strip().upper()
    if symbol in PUBLIC_SYMBOLS:
        ticker = "KRAKEN:" + symbol.replace("/", "")
    elif symbol in FX_SYMBOLS:
        ticker = "FX_IDC:" + symbol.replace("/", "")
    elif re.fullmatch(r"(?:[A-Z0-9_]{2,20}:)?[A-Z0-9][A-Z0-9.\-^]{0,24}", symbol):
        ticker = f"{LISTINGS[symbol]}:{symbol}" if symbol in LISTINGS else symbol
    else:
        return ""
    return "https://www.tradingview.com/chart/?" + urlencode({"symbol": ticker})
