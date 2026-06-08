#!/usr/bin/env python3
"""Headless XAU/USD trading daemon — runs silently in the background.

Usage:
  python daemon.py          # live mode
  python daemon.py --demo   # demo mode (synthetic data, no internet needed)
"""

import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "xau_bot.log"

# Rotating log — 1 MB × 3 files
_handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[_handler, logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("xau_bot")

import data as mdata
from paper_trader import PaperTrader
from config import REFRESH_SECONDS


def main():
    demo = "--demo" in sys.argv
    if demo:
        mdata.DEMO_MODE = True
        log.info("=== XAU/USD daemon starting — DEMO MODE ===")
    else:
        log.info("=== XAU/USD daemon starting — LIVE MODE ===")

    trader = PaperTrader()
    trader.load(STATE_FILE)
    log.info(f"State loaded — balance: ${trader.balance:,.2f} | trades: {trader.trade_count}")

    prev_signal = None
    tick = 0

    while True:
        try:
            df = mdata.fetch_ohlcv()
            df = mdata.add_indicators(df)
            latest = df.iloc[-1]
            price = latest["Close"]
            signal = mdata.get_signal(df)
            trend = mdata.get_trend(df)

            # Check SL/TP exits
            exited = trader.check_exits(latest["High"], latest["Low"])
            if exited:
                exit_type = "TP" if (
                    (exited.direction == "LONG" and exited.exit_price >= exited.take_profit) or
                    (exited.direction == "SHORT" and exited.exit_price <= exited.take_profit)
                ) else "SL"
                log.info(
                    f"EXIT({exit_type}) {exited.direction} @ {exited.exit_price:.2f} "
                    f"| PnL ${exited.pnl:+.2f} | Balance ${trader.balance:,.2f}"
                )
                trader.save(STATE_FILE)

            # Log signal changes
            if signal != prev_signal:
                log.info(f"Signal: {signal} | Price: {price:.2f} | Trend: {trend}")
                prev_signal = signal

            # Open new position
            if signal == "BUY" and not trader.position:
                t = trader.open_long(price, latest["atr"])
                if t:
                    log.info(
                        f"LONG opened @ {t.entry_price:.2f} "
                        f"| SL {t.stop_loss:.2f} | TP {t.take_profit:.2f}"
                    )
                    trader.save(STATE_FILE)

            elif signal == "SELL" and not trader.position:
                t = trader.open_short(price, latest["atr"])
                if t:
                    log.info(
                        f"SHORT opened @ {t.entry_price:.2f} "
                        f"| SL {t.stop_loss:.2f} | TP {t.take_profit:.2f}"
                    )
                    trader.save(STATE_FILE)

            # Flip on opposite signal
            elif signal in ("BUY", "SELL") and trader.position:
                cur = trader.position.direction
                if (signal == "SELL" and cur == "LONG") or (signal == "BUY" and cur == "SHORT"):
                    closed = trader.close_position(price)
                    if closed:
                        log.info(
                            f"FLIP {closed.direction}→{signal} @ {price:.2f} "
                            f"| PnL ${closed.pnl:+.2f}"
                        )
                        trader.save(STATE_FILE)

            # Periodic state save every 10 ticks (keeps last_updated fresh)
            tick += 1
            if tick % 10 == 0:
                trader.save(STATE_FILE)

        except Exception as exc:
            log.error(f"Error: {exc}")

        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
