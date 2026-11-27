# Pivluma

Pivluma is a Windows desktop application in development for financial forecasting and market analysis.

## Current status

The Python forecasting engine fits a rolling linear trend to the latest 20 observations. Walk-forward evaluation compares mean absolute error with a last-price baseline. Forecast intervals use empirical errors for each forecast step.

The desktop interface, CSV import, SQLite storage, local analyst assistant, live Twelve Data integration, and Windows package are planned. Live verification requires a Twelve Data plan appropriate for the intended display use.

## Run the tests

From the repository root, with Python 3.10 or later:

```powershell
python -m unittest discover -s tests
```

The forecasting tests use the Python standard library.

## Development

Commit and push directly to `main`. Update this README when features, setup instructions, or project status change.
