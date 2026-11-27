# Pivluma

Pivluma is a Windows desktop application in development for financial forecasting and market analysis.

## Current release

[Pivluma 0.7.0](https://github.com/muratkorayozkan/Pivluma/releases/tag/v0.7.0) is the current release. Download the [portable Windows x64 ZIP](https://github.com/muratkorayozkan/Pivluma/releases/latest), extract it, and run Pivluma.exe.

The Python forecasting engine fits a rolling linear trend to the latest 20 observations. Walk-forward evaluation compares mean absolute error with a last-price baseline. Forecast intervals use empirical errors for each forecast step.

The Turkish and English desktop dashboard and local analyst assistant are available. Kraken public crypto data and configured Twelve Data feeds are available. A portable Windows package can be built from source. Live verification with a Twelve Data account is pending.

## Run the desktop app

From the repository root on Windows with Python 3.10 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe -m pivluma.desktop
```

The dashboard opens with synthetic data and shows recent closing prices, the forecast, its empirical interval, and walk-forward error against a last-price baseline. Change **Forecast steps** to update the analysis. Choose **Export forecast CSV** to save one row per forecast step with the last observed date, forecast and interval bounds. The file uses UTF-8 with a byte order mark for Excel compatibility. To use your own data, enter a symbol and choose **Import CSV**. **Load saved** lists the symbols stored in the Windows application data directory. Select one to reopen its series from SQLite. The command-line database can be selected independently with `--database`.


Use the **Language** menu to switch between **EN · English** and **TR · Türkçe**. The selected language is remembered on this PC. Switching keeps the active prices and forecast horizon.

The dashboard scrolls vertically when the controls, chart, and analyst response exceed the window height.

## Automatic updates on Windows

Install version 0.5.0 or newer once from the Windows ZIP on GitHub Releases. The portable executable checks for the latest stable release in the background. If a newer Windows ZIP is available, Pivluma verifies the GitHub asset's SHA-256 digest and size before preparing the package under the Windows application data directory.

Transient GitHub lookup failures are retried once. The update status distinguishes network, certificate, rate limit, local storage, and package verification problems.

When the update is ready, select **Open update** and confirm. Pivluma opens the prepared version, keeps the existing executable in place, and remembers the selected version when you reopen an older update-capable copy. Saved price series and the language preference are shared across versions. Source runs continue to use the repository checkout.

## Build the Windows package

On 64-bit Windows with 64-bit Python 3.10 or newer, run these commands from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\scripts\build-windows.ps1 -Python .\.venv\Scripts\python.exe
```

The script builds and smoke-tests `dist/Pivluma/Pivluma.exe`, then creates `dist/Pivluma-0.7.0-Windows.zip` and prints its SHA-256 hash. Extract the entire archive and open `Pivluma.exe` inside the extracted folder. Keep the neighboring `_internal` folder with the executable. The build is unsigned and includes its own Python runtime.


## Connect live market data

Use **Choose symbol** to browse by instrument name and code, or enter a single code manually. **Connect live** opens the picker if the symbol field is empty.

**BTC/USD**, **ETH/USD**, **SOL/USD**, **XRP/USD**, and **DOGE/USD** connect directly to Kraken public market data without an account. Other symbols use Twelve Data if the installation already supplies `TWELVE_DATA_API_KEY` in the process environment and the account permits display and streaming for that symbol. The app never prompts for a key.

The chart uses daily REST history. If daily history is unavailable, Pivluma still tries the quote subscription and displays the history error separately from the stream state. The WebSocket quote is displayed separately with its UTC timestamp and marked as the last received quote after two minutes. Disconnecting keeps the latest quote visible and labels it as the last received quote. Switching to a sample or CSV series closes the live connection and clears the quote. The WebSocket connection and subscription acknowledgement have a combined 15-second deadline. If the provider rejects the subscription or the deadline expires, the socket closes and **Connect live** can retry. Once the subscription is accepted, the app can wait for a quote while the market is quiet. Sample data and CSV analysis work offline.

Provider references: [Kraken daily history](https://docs.kraken.com/api/docs/rest-api/get-ohlc-data), [Kraken ticker stream](https://docs.kraken.com/api/docs/websocket-v2/ticker), [REST API](https://twelvedata.com/docs), [WebSocket API](https://twelvedata.com/docs/websocket/websocket-getting-started), and [plans](https://twelvedata.com/pricing).


## Ask the local analyst

Use the analyst topic buttons or enter an English or Turkish question. The moving average uses the last 20 closes (or all available closes if fewer). Replies use the selected interface language. Try “How did the price move?”, “What is the forecast?”, or “How accurate was the model?”. Replies use the active series, date range, and forecast horizon. The assistant matches supported topics and computes answers locally, with no account or network connection required.


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

Commit and push directly to `main`. Update this README when features, setup instructions, or project status change. For each release, update the application version, verify the tests and Windows build, then tag the verified commit on main as vMAJOR.MINOR.PATCH and publish GitHub release notes with the verified Windows x64 ZIP attached.
