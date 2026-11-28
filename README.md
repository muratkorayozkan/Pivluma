# Pivluma

Pivluma is a Windows desktop application in development for financial forecasting and market analysis.

![Pivluma 0.19.0 showing the llama.cpp projection and a research analysis alongside sample prices](docs/images/pivluma-dashboard.png)

## Download Pivluma 0.19.0

| Windows x64 package | How to use it |
| --- | --- |
| [Setup EXE](https://github.com/muratkorayozkan/Pivluma/releases/download/v0.19.0/Pivluma-0.19.0-Windows-Setup.exe) | Installs to `C:\Program Files\Pivluma` and adds a Start menu entry. The desktop shortcut is selected by default. The local model download is optional. Windows requests administrator approval for installation. |
| [Portable ZIP](https://github.com/muratkorayozkan/Pivluma/releases/download/v0.19.0/Pivluma-0.19.0-Windows.zip) | Extract the entire archive and run `Pivluma.exe`. Keep the neighboring `_internal` folder. |
| [Standalone MSI](https://github.com/muratkorayozkan/Pivluma/releases/download/v0.19.0/Pivluma-0.19.0-Windows-Setup.msi) | Install with Windows Installer. To add the local model, use Pivluma Settings after setup. |

The app opens on BTC/USD. Use the symbol picker to switch instruments or choose **Sample data** to explore offline. The dashboard and built-in analyst support English, Turkish, and Simplified Chinese. [Release notes](https://github.com/muratkorayozkan/Pivluma/releases/tag/v0.19.0)

## How to read the chart

| Chart element | Meaning |
| --- | --- |
| Solid line | Observed closing prices. |
| Statistical reference | Walk-forward errors and XLSX export describe the statistical forecast. The chart displays observed prices and the optional llama.cpp projection. |
| Dash-dot line | Optional llama.cpp projection. It uses recent price returns and passes available research scores to the model. Pivluma estimates a small, fading adjustment only when past matched returns support an association. The projection has no separate backtest. Dotted scenario bounds around the llama line reuse statistical error widths. |

The screenshot uses synthetic prices and sample research scores. [Research inputs and source labels](#use-news-in-a-forecast)

## Run the desktop app

From the repository root on Windows with Python 3.10 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe -m pivluma.desktop
```

The application starts maximized with standard Windows caption buttons and connects to BTC/USD. Press **Esc** to restore the window size or **F11** to toggle maximization. Choose **Sample data** to work with a synthetic series. The chart shows recent closes and, when the local model is installed and a projection is available, the llama.cpp line and its scenario band. Walk-forward errors appear in the statistical reference panel. Change **Forecast steps** to update the analysis. The maximum is 120 steps when there are enough observations for walk-forward evaluation. Choose **Export forecast XLSX** to save an Excel workbook with the active symbol, last observation date, selected model, error metrics, forecasts, and interval bounds. To use your own data, enter a symbol and choose **Import CSV**. **Load saved** lists the symbols stored in the Windows application data directory. Select one to reopen its series from SQLite. The command-line database can be selected independently with `--database`.

Use the **Language** menu to switch between **EN · English**, **TR · Türkçe**, and **简体中文**. The selected language is remembered on this PC. Switching keeps the active prices and forecast horizon.

The chart and analyst appear side by side in wide windows and in separate tabs in compact windows. Choose **Chart history** to change the number of closing prices displayed. Use the mouse wheel over the plot to zoom and drag the plot left or right to move through history. Hover over a point for details, or click to select it. Use **Reset chart** to return to the latest 120 observations and automatic price scaling. Layer toggles control observed closes, the 20-observation moving average, and the llama.cpp line and scenario band. The chart follows the Windows animation preference. When the view moves away from the latest close, it shows historical data for that period. Click any historical or forecast point to inspect its value and, for a forecast, its interval. **Price axis** can set the lower and upper limits or restore automatic scaling.

The upper-right controls open revision history, the about page, and settings. **Settings → Theme** includes a Windows system option and light and dark palettes. The theme is remembered on this PC. Revision history loads recent published GitHub release notes. **About** shows the running version and package type and lets you copy those details, check for updates, view the release notes, or report a problem.

Open **Watchlist** beside the symbol picker to save the current symbol or reopen a saved one. The list is stored on this PC. Selecting a saved symbol updates the same chart, forecast, and analyst context used by the symbol picker. Use **Chart focus** or **Ctrl+Shift+F** to make more room for the chart. The price summary shows the change from the previous observation, which can be an earlier working day for FX.

Use **Compare symbol** to overlay a saved symbol or a public Kraken/ECB symbol. The purple dashed line is rebased to the primary closing price at the first visible date shared by both series. The comparison does not change the inputs to forecasting. Launching Pivluma again brings the running window forward.

[Interface research and design decisions](docs/interface-research.md)

The dashboard scrolls vertically when the controls, chart, and analyst response exceed the window height.

## Automatic updates on Windows

The packaged Windows app checks for the latest stable release in the background. You can also check from the upper toolbar or **About**. If a newer Windows ZIP is available, Pivluma verifies the GitHub asset's SHA-256 digest and size before preparing the package under the Windows application data directory.

Transient GitHub lookup failures are retried once. An icon beside **Check for updates** shows the current status. Hover over it for details. The update status distinguishes network, certificate, rate limit, local storage, and package verification problems.

When the update is ready, select **Open update** and confirm. Pivluma opens the prepared version, keeps the existing executable in place, and remembers the selected version when you reopen an older update-capable copy. Saved price series and the language preference are shared across versions. Source runs continue to use the repository checkout.

Use **Shortcuts** at the top right or press **F1** to view the keyboard commands. The app follows the Windows animation preference.

## Build the Windows package

On 64-bit Windows with 64-bit Python 3.10 or newer, run these commands from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\scripts\build-windows.ps1 -Python .\.venv\Scripts\python.exe
```

The script builds and smoke-tests `dist/Pivluma/Pivluma.exe`, then creates `dist/Pivluma-0.19.0-Windows.zip` and prints its SHA-256 hash. Extract the entire archive and open `Pivluma.exe` inside the extracted folder. Keep the neighboring `_internal` folder with the executable. The build is unsigned and includes its own Python runtime.

To build the themed Setup EXE and standalone MSI after building the portable package, run `./scripts/build-setup.ps1 -WixBin PATH_TO_WIX_BINARIES` with the WiX 3.14.1 binaries. This creates `dist/Pivluma-0.19.0-Windows-Setup.exe` and `dist/Pivluma-0.19.0-Windows-Setup.msi`.

## Connect live market data

Choose an instrument from the **Symbol** drop-down to switch the live chart automatically, or type a single code and press Enter. The live feed changes when you choose a symbol. Choose the same symbol again to retry a failed connection. Forecasting and export become available once sufficient history for the selected symbol arrives.

The symbol picker includes public Kraken crypto pairs against USD or EUR and six FX pairs. With a standard installation, Kraken supplies crypto history and quotes and the [European Central Bank](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) supplies daily FX reference rates. ECB rates are published on working days and are labeled as reference rates. When both currencies differ from EUR, Pivluma computes the cross rate from their EUR rates.

For installations managed by a data provider or administrator, an optional `TWELVE_DATA_API_KEY` in the process environment enables supported equity, ETF and FX data under the provider account's display rights. The public installer requires no key. You can also import equity and ETF history from CSV.

The chart uses daily REST history. If daily history is unavailable, Pivluma still tries the quote subscription and displays the history error separately from the stream state. The WebSocket quote is displayed separately in the Windows local time zone, with its UTC offset and marked as the last received quote after two minutes. Disconnecting keeps the latest quote visible and labels it as the last received quote. Switching to a sample or CSV series closes the live connection and clears the quote. The WebSocket connection and subscription acknowledgement have a combined 15-second deadline. If the provider rejects the subscription or the deadline expires, Twelve Data falls back to a REST quote when available. The app polls once per minute and labels the result as a one-minute bar. For other providers, reselect the symbol to retry. Once the subscription is accepted, the app can wait for a quote while the market is quiet. Sample data and CSV analysis work offline.

Provider references: [Kraken daily history](https://docs.kraken.com/api/docs/rest-api/get-ohlc-data), [Kraken ticker stream](https://docs.kraken.com/api/docs/websocket-v2/ticker), [REST API](https://twelvedata.com/docs), [WebSocket API](https://twelvedata.com/docs/websocket/websocket-getting-started), and [plans](https://twelvedata.com/pricing).

## Cloud analyst

Select **Settings → Analyst model → Cloud analyst**, enter the base HTTPS URL of your deployed Pivluma gateway (for example, `https://analyst.example.com`) and the access code issued by its operator. No public gateway is configured by default. Pivluma appends the API path. The operator configures the SHA-256 hash of the access code as `PIVLUMA_ACCESS_SHA256` and sets `OPENAI_API_KEY` on the gateway server. The code stays in memory for the session. The calculated answer remains available if the service is down. A hosted gateway is required. [Server deployment](server/README.md)

## Use news in a forecast

For a supported live symbol the app requests daily news tone from [GDELT DOC 2.0](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/). If GDELT rate-limits a request, Pivluma respects the retry delay and uses saved GDELT scores when available. BTC can fall back to the separately labeled [Alternative.me Crypto Fear & Greed Index](https://alternative.me/crypto/fear-and-greed-index/), a market sentiment measure. Select **Research news** to retry or to start research from a saved price series. **Import news scores** also accepts a UTF-8 CSV with `date,tone` columns, ISO dates, and numeric tone scores from −100 to +100. Provide one score per day and use scores available at that time.

The forecast matches each historical price observation to the previous calendar day's news score. After at least 20 matched returns it can compare a bounded, news-adjusted candidate with the existing price models in walk-forward evaluation. The data panel shows the source, number of matched observations, and whether the current prediction uses the news model. Missing news leaves price-based forecasting available. Ask the analyst about news for the current result.

When llama.cpp is selected, the chart also generates a local-model projection. You can rerun it with **Run llama.cpp forecast**. It gives the model the latest returns and up to 14 recent research scores available before the last price date. The chart labels the research source and draws the local model in a dash-dot line. The model returns estimates at up to five steps. Pivluma compares returns after positive and negative scores. A fading adjustment of at most 0.5 percentage points at the first step requires at least 20 matched observations, at least five positive and five negative cases, and an absolute historical correlation of at least 0.15. The model also receives a comparison with completed historical periods matched by 5- and 20-observation returns, volatility, and research tone where available. The analysis reports the number of matches and the range of subsequent outcomes. When at least eight close matches mostly point in one direction and the pattern method beats a flat-price reference in recent walk-forward tests, the chart moves up to 20% of the way from the model estimate toward the historical median return. Research scores are aggregates. The **Analyze news** shortcut explains the evidence and its limits. The llama.cpp scenario band uses statistical error widths. The walk-forward errors and XLSX export describe the statistical reference.

## Ask the local analyst

Use the analyst topic buttons or enter an English, Turkish, or Simplified Chinese question. **Visible period** summarizes the closing prices currently shown in the chart. **Selected point** explains the highlighted historical or forecast observation. Both shortcuts follow the chart as you change the selected point or visible period. The moving average uses the last 20 closes (or all available closes if fewer). Replies use the selected interface language. Try “How did the price move?”, “What is the forecast?”, or “How accurate was the model?”. Replies use the active series, date range, and forecast horizon. The built-in analyst computes answers offline, including per-observation volatility, maximum drawdown, highest and lowest closing prices, and 14-observation RSI using simple averages.

For natural-language explanations, optionally download the local llama.cpp engine and Qwen3 1.7B (4-bit) by selecting the checkbox in **Windows Setup EXE** or choosing **Settings → Download local model**. The portable and standalone MSI builds support the in-app download. The download is approximately 1.31 GB and needs 2 GB of free disk space. It is available for Windows x64 and uses the CPU. Pivluma verifies both downloads and stores them in the current user's Pivluma application data folder. Only the package and model download require internet access.

A successful model download selects the local model. Existing installations with a downloaded model switch to it once after upgrading. Pivluma defaults to the built-in analyst when no local model is installed. You can explicitly choose the built-in or cloud analyst in Settings. The built-in calculations remain visible above generated explanations. The local engine runs on loopback, starts on demand, stops after two idle minutes, and shuts down when Pivluma closes.

[llama.cpp release](https://github.com/ggml-org/llama.cpp/releases/tag/b12823) · [Qwen3 model](https://huggingface.co/ggml-org/Qwen3-1.7B-GGUF)

## Try offline analysis

With Python 3.10 or newer, run these commands from the repository root:

```powershell
python -m pivluma --horizon 5
python -m pivluma --import-csv prices.csv --symbol DEMO --horizon 5
python -m pivluma --load --symbol DEMO --horizon 5
```

The first command uses 120 synthetic closing prices. The import expects a UTF-8 CSV with `date` and `close` columns. Use `YYYY-MM-DD` dates and positive finite closing prices. Each date must appear once. A five-step forecast needs at least 34 observations. Steps represent observations in the series.

A successful import replaces the stored series for that symbol in `pivluma.sqlite3`. Use `--database PATH` to choose another file. The output includes forecasts, empirical intervals and both walk-forward error scores.

## Run the tests

From the repository root, with Python 3.10 or later:

```powershell
python -m unittest discover -s tests
```

The data and forecasting tests use the Python standard library. Run the same command with the desktop dependencies installed to include the Qt tests. The market integration test runs local REST and WebSocket servers on loopback. It verifies the history request, subscription, quote timestamp, and disconnect without a Twelve Data account.

## Development

Commit and push directly to `main`. Update this README when features, setup instructions, or project status change. For each release, update the application version, verify the tests and Windows build, then tag the verified commit on main as vMAJOR.MINOR.PATCH and publish GitHub release notes with the verified Windows x64 ZIP, Setup EXE, and MSI attached.

The Pivluma wordmark uses Bricolage Grotesque. [Font license](pivluma/fonts/OFL.txt)
