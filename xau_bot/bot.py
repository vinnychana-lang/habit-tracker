#!/usr/bin/env python3
"""XAU/USD Trading Bot — terminal UI with paper trading.

Usage:
  python bot.py           # live mode (requires internet + yfinance)
  python bot.py --demo    # demo mode with synthetic price data
"""

import sys
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

import data as mdata
from paper_trader import PaperTrader
from config import (
    DISPLAY_NAME, REFRESH_SECONDS,
    EMA_FAST, EMA_SLOW, RSI_PERIOD,
    RSI_OVERBOUGHT, RSI_OVERSOLD,
    STOP_LOSS_ATR_MULT, TAKE_PROFIT_ATR_MULT,
)

console = Console()
trader = PaperTrader()
signal_history: list[tuple[str, str, float]] = []  # (time, signal, price)


# ── Formatting helpers ──────────────────────────────────────────────────────

def fmt_price(v: float) -> str:
    return f"{v:,.2f}"


def pnl_color(v: float) -> str:
    return "green" if v >= 0 else "red"


def signal_style(s: str) -> str:
    return {"BUY": "bold green", "SELL": "bold red", "HOLD": "bold yellow"}.get(s, "white")


def trend_style(t: str) -> str:
    return {"BULLISH": "green", "BEARISH": "red", "NEUTRAL": "yellow"}.get(t, "white")


# ── Panel builders ──────────────────────────────────────────────────────────

def build_header(price: float, change: float, change_pct: float, trend: str) -> Panel:
    direction = "▲" if change >= 0 else "▼"
    color = "green" if change >= 0 else "red"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M:%S UTC")

    t = Text()
    t.append(f"  {DISPLAY_NAME}  ", style="bold white on dark_blue")
    t.append(f"  {fmt_price(price)}  ", style=f"bold {color}")
    t.append(f"{direction} {fmt_price(abs(change))}  ({change_pct:+.2f}%)  ", style=color)
    t.append(f" Trend: ")
    t.append(f"{trend}  ", style=trend_style(trend))
    t.append(f"  {now}", style="dim")
    return Panel(t, box=box.DOUBLE_EDGE, style="bold")


def build_indicators(df) -> Panel:
    latest = df.iloc[-1]
    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", expand=True)
    tbl.add_column("Indicator", style="cyan")
    tbl.add_column("Value", justify="right")
    tbl.add_column("Signal", justify="center")

    # EMA
    ema_sig = "▲ Fast > Slow" if latest["ema_fast"] > latest["ema_slow"] else "▼ Fast < Slow"
    ema_col = "green" if latest["ema_fast"] > latest["ema_slow"] else "red"
    tbl.add_row(f"EMA {EMA_FAST}", fmt_price(latest["ema_fast"]), Text(ema_sig, style=ema_col))
    tbl.add_row(f"EMA {EMA_SLOW}", fmt_price(latest["ema_slow"]), "")

    # RSI
    rsi_val = latest["rsi"]
    if rsi_val >= RSI_OVERBOUGHT:
        rsi_sig, rsi_col = "Overbought", "red"
    elif rsi_val <= RSI_OVERSOLD:
        rsi_sig, rsi_col = "Oversold", "green"
    else:
        rsi_sig, rsi_col = "Neutral", "yellow"
    tbl.add_row(f"RSI ({RSI_PERIOD})", f"{rsi_val:.1f}", Text(rsi_sig, style=rsi_col))

    # ATR
    tbl.add_row("ATR (14)", fmt_price(latest["atr"]), "")

    # MACD
    macd_col = "green" if latest["macd_diff"] > 0 else "red"
    macd_sig = "Bullish" if latest["macd_diff"] > 0 else "Bearish"
    tbl.add_row("MACD diff", f"{latest['macd_diff']:.4f}", Text(macd_sig, style=macd_col))

    # Bollinger
    tbl.add_row("BB Upper", fmt_price(latest["bb_upper"]), "")
    tbl.add_row("BB Mid", fmt_price(latest["bb_mid"]), "")
    tbl.add_row("BB Lower", fmt_price(latest["bb_lower"]), "")

    return Panel(tbl, title="[bold cyan]Technical Indicators[/]", box=box.ROUNDED)


def build_signal_panel(signal: str) -> Panel:
    style = signal_style(signal)
    icons = {"BUY": "⬆  LONG SIGNAL", "SELL": "⬇  SHORT SIGNAL", "HOLD": "◆  NO SIGNAL"}
    msg = icons.get(signal, signal)
    body = Text(f"\n   {msg}\n", style=style, justify="center")
    return Panel(body, title="[bold white]Signal[/]", box=box.HEAVY, border_style=style.replace("bold ", ""))


def build_account(df) -> Panel:
    latest = df.iloc[-1]
    price = latest["Close"]
    unrealized = trader.unrealized(price)

    tbl = Table(box=box.SIMPLE, show_header=False, expand=True)
    tbl.add_column("Label", style="dim")
    tbl.add_column("Value", justify="right")

    tbl.add_row("Balance", Text(f"${trader.balance:,.2f}", style="bold white"))
    tbl.add_row("Realized P&L", Text(f"${trader.total_pnl:+,.2f}", style=pnl_color(trader.total_pnl)))
    tbl.add_row("Unrealized P&L", Text(f"${unrealized:+,.2f}", style=pnl_color(unrealized)))
    tbl.add_row("Total Trades", str(trader.trade_count))
    tbl.add_row("Win Rate", f"{trader.win_rate:.1f}%")

    if trader.position:
        p = trader.position
        tbl.add_row("─" * 10, "─" * 10)
        tbl.add_row("Position", Text(p.direction, style="bold green" if p.direction == "LONG" else "bold red"))
        tbl.add_row("Entry", fmt_price(p.entry_price))
        tbl.add_row("Stop Loss", Text(fmt_price(p.stop_loss), style="red"))
        tbl.add_row("Take Profit", Text(fmt_price(p.take_profit), style="green"))
        qty_usd = p.quantity * p.entry_price
        tbl.add_row("Size (USD)", f"${qty_usd:,.2f}")

    return Panel(tbl, title="[bold green]Paper Account[/]", box=box.ROUNDED)


def build_history() -> Panel:
    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold magenta", expand=True)
    tbl.add_column("Time", style="dim", width=10)
    tbl.add_column("Signal", justify="center", width=6)
    tbl.add_column("Price", justify="right")

    rows = signal_history[-12:]
    for ts, sig, px in reversed(rows):
        tbl.add_row(ts, Text(sig, style=signal_style(sig)), fmt_price(px))

    return Panel(tbl, title="[bold magenta]Signal History[/]", box=box.ROUNDED)


def build_trades() -> Panel:
    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold yellow", expand=True)
    tbl.add_column("Dir", width=5)
    tbl.add_column("Entry", justify="right")
    tbl.add_column("Exit", justify="right")
    tbl.add_column("P&L", justify="right")

    trades = trader.closed_trades[-8:]
    for t in reversed(trades):
        dir_style = "green" if t.direction == "LONG" else "red"
        pnl_str = f"${t.pnl:+,.2f}" if t.pnl is not None else "—"
        tbl.add_row(
            Text(t.direction[0], style=dir_style),
            fmt_price(t.entry_price),
            fmt_price(t.exit_price) if t.exit_price else "—",
            Text(pnl_str, style=pnl_color(t.pnl or 0)),
        )

    return Panel(tbl, title="[bold yellow]Closed Trades[/]", box=box.ROUNDED)


def build_mini_chart(df) -> Panel:
    """ASCII sparkline of the last 40 closes."""
    closes = df["Close"].tail(40).tolist()
    if not closes:
        return Panel("No data", title="Price Chart")

    hi, lo = max(closes), min(closes)
    spread = hi - lo or 1
    height = 8
    bars = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

    line = ""
    for c in closes:
        idx = int((c - lo) / spread * (height - 1))
        line += bars[idx]

    text = Text()
    text.append(f"Hi: {fmt_price(hi)}  Lo: {fmt_price(lo)}\n", style="dim")
    text.append(line + "\n", style="green")
    text.append(f"  ←── last 40 × 5m candles ───►", style="dim")

    return Panel(text, title="[bold]XAU Sparkline[/]", box=box.ROUNDED)


# ── Main render ─────────────────────────────────────────────────────────────

def render(df) -> Layout:
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    price = latest["Close"]
    change = price - prev["Close"]
    change_pct = change / prev["Close"] * 100
    signal = mdata.get_signal(df)
    trend = mdata.get_trend(df)

    # Auto-trade on signal
    last_sig = signal_history[-1][1] if signal_history else None
    now_str = datetime.now().strftime("%H:%M:%S")

    if signal != last_sig or not signal_history:
        signal_history.append((now_str, signal, price))

    if signal == "BUY" and not trader.position:
        trader.open_long(price, latest["atr"])
    elif signal == "SELL" and not trader.position:
        trader.open_short(price, latest["atr"])
    elif signal in ("BUY", "SELL") and trader.position:
        cur_dir = trader.position.direction
        if (signal == "SELL" and cur_dir == "LONG") or (signal == "BUY" and cur_dir == "SHORT"):
            trader.close_position(price)

    trader.check_exits(latest["High"], latest["Low"])

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=5),
    )
    layout["body"].split_row(
        Layout(name="left", ratio=2),
        Layout(name="right", ratio=3),
    )
    layout["left"].split_column(
        Layout(name="signal", size=7),
        Layout(name="account"),
    )
    layout["right"].split_column(
        Layout(name="indicators"),
        Layout(name="chart", size=7),
    )
    layout["footer"].split_row(
        Layout(name="history"),
        Layout(name="trades"),
    )

    layout["header"].update(build_header(price, change, change_pct, trend))
    layout["signal"].update(build_signal_panel(signal))
    layout["account"].update(build_account(df))
    layout["indicators"].update(build_indicators(df))
    layout["chart"].update(build_mini_chart(df))
    layout["history"].update(build_history())
    layout["trades"].update(build_trades())

    return layout


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    import data as _data_module
    if "--demo" in sys.argv:
        _data_module.DEMO_MODE = True
        mode_label = "[bold yellow]DEMO MODE[/] (synthetic data)"
    else:
        mode_label = "[bold green]LIVE MODE[/] (Yahoo Finance)"

    console.print(Panel(
        f"[bold gold1]XAU/USD Trading Bot[/]  {mode_label}\n[dim]Fetching data…[/]",
        box=box.DOUBLE_EDGE
    ))

    try:
        with Live(console=console, refresh_per_second=1, screen=True) as live:
            while True:
                try:
                    df = mdata.fetch_ohlcv()
                    df = mdata.add_indicators(df)
                    live.update(render(df))
                except Exception as exc:
                    live.update(Panel(f"[red]Error:[/] {exc}\nRetrying in {REFRESH_SECONDS}s…"))

                time.sleep(REFRESH_SECONDS)

    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped.[/]")
        console.print(f"Final balance: [bold]${trader.balance:,.2f}[/]")
        console.print(f"Total P&L:     [bold]{'+' if trader.total_pnl >= 0 else ''}${trader.total_pnl:,.2f}[/]")
        console.print(f"Win rate:      [bold]{trader.win_rate:.1f}%[/]  ({trader.trade_count} trades)")
        sys.exit(0)


if __name__ == "__main__":
    main()
