# XAU/USD Trading Bot

A terminal-based paper trading bot for Gold (XAU/USD) with live price data, technical indicators, and automated signal generation.

![Terminal UI](../docs/xau_bot_preview.png)

## Features

- **Live price data** via Yahoo Finance (GC=F Gold Futures)
- **Technical indicators**: EMA 9/21 crossover, RSI(14), MACD, ATR(14), Bollinger Bands
- **Signal engine**: BUY/SELL/HOLD based on EMA crossover + RSI + MACD confluence
- **Paper trading**: Auto-trades signals, tracks P&L, win rate, open positions
- **Risk management**: ATR-based stop-loss (1.5×ATR) and take-profit (3×ATR)
- **Rich terminal UI**: Live-updating dashboard with sparkline chart
- **Demo mode**: Synthetic GBM price data — no internet required

## Strategy Logic

| Condition | Signal |
|-----------|--------|
| EMA9 crosses above EMA21 + RSI < 65 + MACD histogram > 0 | **BUY** |
| EMA9 crosses below EMA21 + RSI > 35 + MACD histogram < 0 | **SELL** |
| Otherwise | **HOLD** |

Position sizing: 10% of balance per trade  
Stop-loss: entry ± 1.5 × ATR  
Take-profit: entry ± 3.0 × ATR

## Setup

```bash
cd xau_bot
pip install -r requirements.txt
```

## Usage

```bash
# Live mode (requires internet)
python bot.py

# Demo mode (synthetic data, works offline)
python bot.py --demo
```

Press `Ctrl+C` to stop. Final P&L summary printed on exit.

## Configuration

Edit `config.py` to change:
- `SYMBOL` — default `GC=F` (Gold Futures); try `XAUUSD=X` for spot
- `INTERVAL` — candle timeframe (`1m`, `5m`, `15m`, `1h`)
- `STARTING_BALANCE` — paper trading balance
- `POSITION_SIZE_PCT` — fraction of balance per trade
- `REFRESH_SECONDS` — how often to poll for new data

## Disclaimer

This is a paper trading / educational tool. It does **not** execute real trades. Past simulated performance does not indicate future results.
