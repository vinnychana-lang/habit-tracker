SYMBOL = "GC=F"          # Gold Futures (XAU/USD proxy)
DISPLAY_NAME = "XAU/USD"
INTERVAL = "5m"          # 5-minute candles
LOOKBACK_PERIOD = "5d"   # 5 days of history for indicators

# Strategy parameters
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
ATR_PERIOD = 14

RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# Risk management (paper trading)
STARTING_BALANCE = 10_000.0   # USD
POSITION_SIZE_PCT = 0.10      # 10% of balance per trade
STOP_LOSS_ATR_MULT = 1.5
TAKE_PROFIT_ATR_MULT = 3.0

# Refresh interval in seconds
REFRESH_SECONDS = 30
