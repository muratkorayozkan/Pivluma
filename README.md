# Pivluma

Pivluma is a Windows desktop application in development for financial forecasting and market analysis.

## Current status

The Python forecasting engine fits a rolling linear trend to the latest 20 observations. Walk-forward evaluation compares mean absolute error with a last-price baseline. Forecast intervals use empirical errors for each forecast step.

The desktop interface, local analyst assistant, live Twelve Data integration, and Windows package are planned. Live verification requires a Twelve Data plan appropriate for the intended display use.

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

The forecasting tests use the Python standard library.

## Development

Commit and push directly to `main`. Update this README when features, setup instructions, or project status change.
