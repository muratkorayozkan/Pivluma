import unittest
from urllib.parse import parse_qs, urlsplit

from pivluma.tradingview import tradingview_url


class TradingViewLinkTests(unittest.TestCase):
    def test_stock_and_forex_links_use_expected_tickers(self):
        for symbol, expected in (("nflx", "NASDAQ:NFLX"), ("META", "NASDAQ:META"),
                                 ("BTC/USD", "KRAKEN:BTCUSD"), ("USD/TRY", "FX_IDC:USDTRY")):
            parsed = urlsplit(tradingview_url(symbol))
            self.assertEqual(parsed.hostname, "www.tradingview.com")
            self.assertEqual(parse_qs(parsed.query), {"symbol": [expected]})

    def test_invalid_symbol_cannot_change_the_destination(self):
        self.assertEqual(tradingview_url("NFLX&redirect=https://example.com"), "")
