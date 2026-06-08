import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from config import (
    SYMBOL, INTERVAL, LOOKBACK_PERIOD,
    EMA_FAST, EMA_SLOW, RSI_PERIOD, ATR_PERIOD,
    RSI_OVERBOUGHT, RSI_OVERSOLD,
)

# Set to True via --demo flag in bot.py
DEMO_MODE = False
_demo_seed_price = 3315.0   # approximate XAU/USD as of mid-2026


def fetch_ohlcv() -> pd.DataFrame:
    if DEMO_MODE:
        return _generate_demo_ohlcv()

    ticker = yf.Ticker(SYMBOL)
    df = ticker.history(period=LOOKBACK_PERIOD, interval=INTERVAL)
    if df.empty:
        raise RuntimeError(f"No data returned for {SYMBOL}")
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.dropna(inplace=True)
    return df


def _generate_demo_ohlcv(n_bars: int = 200) -> pd.DataFrame:
    """Generate synthetic XAU/USD OHLCV data via Geometric Brownian Motion."""
    np.random.seed(int(datetime.utcnow().timestamp()) // 30)  # changes every 30s

    # GBM parameters (calibrated to gold historical vols)
    mu = 0.00005       # slight upward drift per bar
    sigma = 0.0008     # per-bar volatility (~1.2% daily on 5m candles)

    prices = [_demo_seed_price]
    for _ in range(n_bars - 1):
        ret = np.random.normal(mu, sigma)
        prices.append(prices[-1] * (1 + ret))

    closes = np.array(prices)

    # Build OHLCV from closes
    highs = closes * (1 + np.abs(np.random.normal(0, sigma * 0.6, n_bars)))
    lows = closes * (1 - np.abs(np.random.normal(0, sigma * 0.6, n_bars)))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = np.random.randint(500, 5000, n_bars).astype(float)

    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    idx = [end - timedelta(minutes=5 * i) for i in range(n_bars - 1, -1, -1)]

    return pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    }, index=pd.DatetimeIndex(idx))


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bollinger(series: pd.Series, window: int = 20, std_dev: float = 2.0):
    mid = series.rolling(window).mean()
    std = series.rolling(window).std()
    return mid + std_dev * std, mid, mid - std_dev * std


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = _ema(df["Close"], EMA_FAST)
    df["ema_slow"] = _ema(df["Close"], EMA_SLOW)
    df["rsi"] = _rsi(df["Close"], RSI_PERIOD)
    df["atr"] = _atr(df["High"], df["Low"], df["Close"], ATR_PERIOD)
    df["macd"], df["macd_signal"], df["macd_diff"] = _macd(df["Close"])
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = _bollinger(df["Close"])
    df.dropna(inplace=True)
    return df


def get_signal(df: pd.DataFrame) -> str:
    if len(df) < 2:
        return "HOLD"

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    ema_cross_up = (prev["ema_fast"] <= prev["ema_slow"]) and (latest["ema_fast"] > latest["ema_slow"])
    ema_cross_down = (prev["ema_fast"] >= prev["ema_slow"]) and (latest["ema_fast"] < latest["ema_slow"])
    rsi_ok_buy = latest["rsi"] < 65
    rsi_ok_sell = latest["rsi"] > 35
    macd_bullish = latest["macd_diff"] > 0
    macd_bearish = latest["macd_diff"] < 0

    if ema_cross_up and rsi_ok_buy and macd_bullish:
        return "BUY"
    if ema_cross_down and rsi_ok_sell and macd_bearish:
        return "SELL"
    return "HOLD"


def get_trend(df: pd.DataFrame) -> str:
    latest = df.iloc[-1]
    if latest["ema_fast"] > latest["ema_slow"]:
        return "BULLISH"
    if latest["ema_fast"] < latest["ema_slow"]:
        return "BEARISH"
    return "NEUTRAL"
