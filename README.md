# Pivluma

Pivluma is a Windows desktop application in development for financial forecasting and market analysis.

## Current status

The Python forecasting engine fits a rolling linear trend to the latest 20 observations. Walk-forward evaluation compares mean absolute error with a last-price baseline. Forecast intervals use empirical errors for each forecast step.

The English desktop dashboard and local analyst assistant are available. Twelve Data integration is available. A live account check and the Windows package are pending.

## Run the desktop app

From the repository root on Windows with Python 3.10 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe -m pivluma.desktop
```

The dashboard opens with synthetic data and shows recent closing prices, the forecast, its empirical interval, and walk-forward error against a last-price baseline. Change **Forecast steps** to update the analysis. To use your own data, enter a symbol and choose **Import CSV**. **Load saved** reopens that symbol from SQLite in the Windows application data directory. The command-line database can be selected independently with `--database`.


## Connect live market data

Enter a symbol such as **AAPL**, **EUR/USD**, or **BTC/USD**, select **Connect live**, and enter a Twelve Data API key in the masked prompt. Use an account plan that permits display and streaming for the selected symbol. The key is used for the current connection attempt and is not saved.

The chart uses daily REST history. The WebSocket quote is displayed separately with its UTC timestamp and marked as the last received quote after two minutes. Disconnecting keeps the latest quote visible for the current session. Reconnect with **Connect live**. Sample data and CSV analysis work offline.

Provider references: [REST API](https://twelvedata.com/docs), [WebSocket API](https://twelvedata.com/docs/websocket/websocket-getting-started), and [plans](https://twelvedata.com/pricing).


## Ask the local analyst

Enter an English question in **Local analyst** and select **Ask**. Try “How did the price move?”, “What is the forecast?”, or “How accurate was the model?”. Replies use the active series, date range, and forecast horizon. The assistant matches supported topics and computes answers locally, with no account or network connection required.


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

The data and forecasting tests use the Python standard library. Run the same command with the desktop dependencies installed to include the Qt tests.

## Development

Commit and push directly to `main`. Update this README when features, setup instructions, or project status change.
