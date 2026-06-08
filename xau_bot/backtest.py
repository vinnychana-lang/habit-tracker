#!/usr/bin/env python3
"""XAU/USD Strategy Backtester

Usage:
  python backtest.py            # synthetic demo data (2000 bars ≈ 1 week of 5m)
  python backtest.py --live     # real data from Yahoo Finance (60d of 5m)
  python backtest.py --bars N   # override bar count for demo mode
  python backtest.py --seed N   # fixed random seed for reproducible demo runs
"""

import sys
import math
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.rule import Rule
from rich import box

import data as mdata
from config import (
    STARTING_BALANCE, POSITION_SIZE_PCT,
    STOP_LOSS_ATR_MULT, TAKE_PROFIT_ATR_MULT,
    EMA_FAST, EMA_SLOW, DISPLAY_NAME,
)

console = Console()


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class BTrade:
    direction: str
    entry_bar: int
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    exit_bar: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None   # "SL", "TP", "SIGNAL", "EOD"
    pnl: Optional[float] = None

    def close(self, bar: int, price: float, reason: str) -> float:
        self.exit_bar = bar
        self.exit_price = price
        self.exit_reason = reason
        if self.direction == "LONG":
            self.pnl = (price - self.entry_price) * self.quantity
        else:
            self.pnl = (self.entry_price - price) * self.quantity
        return self.pnl

    @property
    def bars_held(self) -> Optional[int]:
        if self.exit_bar is None:
            return None
        return self.exit_bar - self.entry_bar

    @property
    def r_multiple(self) -> Optional[float]:
        """P&L expressed as multiples of initial risk."""
        risk = abs(self.entry_price - self.stop_loss) * self.quantity
        if self.pnl is None or risk == 0:
            return None
        return self.pnl / risk


# ── Signal generation (inline, no re-computation) ────────────────────────────

def _bar_signal(df: pd.DataFrame, i: int) -> str:
    """Signal for bar i using precomputed indicator columns."""
    if i < 1:
        return "HOLD"
    cur = df.iloc[i]
    prv = df.iloc[i - 1]

    cross_up = prv["ema_fast"] <= prv["ema_slow"] and cur["ema_fast"] > cur["ema_slow"]
    cross_dn = prv["ema_fast"] >= prv["ema_slow"] and cur["ema_fast"] < cur["ema_slow"]

    if cross_up and cur["rsi"] < 65 and cur["macd_diff"] > 0:
        return "BUY"
    if cross_dn and cur["rsi"] > 35 and cur["macd_diff"] < 0:
        return "SELL"
    return "HOLD"


# ── Back-test engine ─────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame) -> tuple[list[BTrade], list[float]]:
    """Walk forward through df bar-by-bar. Returns (trades, equity_curve)."""
    balance = STARTING_BALANCE
    position: Optional[BTrade] = None
    trades: list[BTrade] = []
    equity: list[float] = [balance]

    for i in range(1, len(df)):
        bar = df.iloc[i]
        price = bar["Close"]
        high = bar["High"]
        low = bar["Low"]
        atr = bar["atr"]

        # ── 1. Check SL / TP on open position ──────────────────────────────
        if position:
            hit_price = None
            reason = None
            if position.direction == "LONG":
                if low <= position.stop_loss:
                    hit_price, reason = position.stop_loss, "SL"
                elif high >= position.take_profit:
                    hit_price, reason = position.take_profit, "TP"
            else:
                if high >= position.stop_loss:
                    hit_price, reason = position.stop_loss, "SL"
                elif low <= position.take_profit:
                    hit_price, reason = position.take_profit, "TP"

            if hit_price is not None:
                pnl = position.close(i, hit_price, reason)
                balance += pnl
                trades.append(position)
                position = None

        # ── 2. Generate signal for this bar ────────────────────────────────
        signal = _bar_signal(df, i)

        # ── 3. Flip or open position on signal ─────────────────────────────
        if signal != "HOLD":
            # Close opposing position first
            if position:
                wrong_side = (signal == "SELL" and position.direction == "LONG") or \
                             (signal == "BUY" and position.direction == "SHORT")
                if wrong_side:
                    pnl = position.close(i, price, "SIGNAL")
                    balance += pnl
                    trades.append(position)
                    position = None

            # Open new position
            if position is None:
                capital = balance * POSITION_SIZE_PCT
                qty = capital / price
                if signal == "BUY":
                    sl = price - atr * STOP_LOSS_ATR_MULT
                    tp = price + atr * TAKE_PROFIT_ATR_MULT
                    position = BTrade("LONG", i, price, qty, sl, tp)
                else:
                    sl = price + atr * STOP_LOSS_ATR_MULT
                    tp = price - atr * TAKE_PROFIT_ATR_MULT
                    position = BTrade("SHORT", i, price, qty, sl, tp)

        # ── 4. Record equity (balance + unrealized) ─────────────────────────
        unrealized = 0.0
        if position:
            if position.direction == "LONG":
                unrealized = (price - position.entry_price) * position.quantity
            else:
                unrealized = (position.entry_price - price) * position.quantity
        equity.append(balance + unrealized)

    # Close any open position at the last bar
    if position:
        last = df.iloc[-1]
        pnl = position.close(len(df) - 1, last["Close"], "EOD")
        balance += pnl
        trades.append(position)

    return trades, equity


# ── Metrics ──────────────────────────────────────────────────────────────────

def compute_metrics(trades: list[BTrade], equity: list[float], n_bars: int) -> dict:
    if not trades:
        return {}

    pnls = [t.pnl for t in trades if t.pnl is not None]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]

    total_return = (equity[-1] - equity[0]) / equity[0] * 100
    bars_per_year = 252 * 78  # 78 five-minute bars in a trading day
    years = n_bars / bars_per_year
    if years > 0 and equity[0] > 0:
        ann_return = ((equity[-1] / equity[0]) ** (1 / years) - 1) * 100
    else:
        ann_return = 0.0

    # Max drawdown
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualized, assumes 0 risk-free rate)
    eq_series = pd.Series(equity)
    rets = eq_series.pct_change().dropna()
    if rets.std() > 0:
        sharpe = (rets.mean() / rets.std()) * math.sqrt(bars_per_year)
    else:
        sharpe = 0.0

    # Sortino (downside deviation only)
    neg_rets = rets[rets < 0]
    if len(neg_rets) > 0 and neg_rets.std() > 0:
        sortino = (rets.mean() / neg_rets.std()) * math.sqrt(bars_per_year)
    else:
        sortino = 0.0

    profit_factor = abs(sum(winners)) / abs(sum(losers)) if losers else float("inf")
    avg_win = sum(winners) / len(winners) if winners else 0
    avg_loss = sum(losers) / len(losers) if losers else 0
    avg_hold = sum(t.bars_held for t in trades if t.bars_held) / len(trades)

    r_multiples = [t.r_multiple for t in trades if t.r_multiple is not None]
    avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0

    return {
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": len(winners) / len(trades) * 100,
        "total_pnl": sum(pnls),
        "total_return_pct": total_return,
        "ann_return_pct": ann_return,
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "sortino": sortino,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_hold_bars": avg_hold,
        "avg_r": avg_r,
        "final_balance": equity[-1],
    }


# ── ASCII equity curve ────────────────────────────────────────────────────────

def equity_sparkline(equity: list[float], width: int = 60, height: int = 10) -> str:
    if len(equity) < 2:
        return ""
    # Downsample to `width` points
    step = max(1, len(equity) // width)
    sampled = equity[::step][:width]
    hi, lo = max(sampled), min(sampled)
    spread = hi - lo or 1

    blocks = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    row = ""
    for v in sampled:
        idx = int((v - lo) / spread * (len(blocks) - 1))
        row += blocks[idx]
    return row


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_summary(metrics: dict, equity: list[float], df: pd.DataFrame) -> None:
    start_price = df["Close"].iloc[0]
    end_price = df["Close"].iloc[-1]
    bh_return = (end_price - start_price) / start_price * 100

    # ── Summary stats table ──
    stats = Table(box=box.SIMPLE, show_header=False, expand=True)
    stats.add_column("Metric", style="dim", width=22)
    stats.add_column("Value", justify="right")

    def pnl_txt(v: float) -> Text:
        return Text(f"${v:+,.2f}", style="green" if v >= 0 else "red")

    def pct_txt(v: float) -> Text:
        return Text(f"{v:+.2f}%", style="green" if v >= 0 else "red")

    stats.add_row("Starting balance", f"${STARTING_BALANCE:,.2f}")
    stats.add_row("Final balance", f"${metrics['final_balance']:,.2f}")
    stats.add_row("Total P&L", pnl_txt(metrics["total_pnl"]))
    stats.add_row("Total return", pct_txt(metrics["total_return_pct"]))
    stats.add_row("Annualised return", pct_txt(metrics["ann_return_pct"]))
    stats.add_row("Buy-and-hold return", pct_txt(bh_return))
    stats.add_row("Max drawdown", Text(f"-{metrics['max_drawdown_pct']:.2f}%", style="red"))
    stats.add_row("Sharpe ratio", f"{metrics['sharpe']:.2f}")
    stats.add_row("Sortino ratio", f"{metrics['sortino']:.2f}")

    # ── Trade stats table ──
    trade_stats = Table(box=box.SIMPLE, show_header=False, expand=True)
    trade_stats.add_column("Metric", style="dim", width=22)
    trade_stats.add_column("Value", justify="right")

    trade_stats.add_row("Total trades", str(metrics["total_trades"]))
    trade_stats.add_row("Winners", Text(str(metrics["winners"]), style="green"))
    trade_stats.add_row("Losers", Text(str(metrics["losers"]), style="red"))
    trade_stats.add_row("Win rate", f"{metrics['win_rate']:.1f}%")
    trade_stats.add_row("Profit factor",
        Text(f"{metrics['profit_factor']:.2f}",
             style="green" if metrics["profit_factor"] >= 1 else "red"))
    trade_stats.add_row("Avg winner", pnl_txt(metrics["avg_win"]))
    trade_stats.add_row("Avg loser", pnl_txt(metrics["avg_loss"]))
    trade_stats.add_row("Avg hold (bars)", f"{metrics['avg_hold_bars']:.1f}")
    trade_stats.add_row("Avg R-multiple", f"{metrics['avg_r']:.2f}R")

    console.print(Rule(f"[bold gold1]  {DISPLAY_NAME} Backtest Results  [/]"))
    console.print(Columns([
        Panel(stats, title="[cyan]Performance[/]", box=box.ROUNDED),
        Panel(trade_stats, title="[magenta]Trade Statistics[/]", box=box.ROUNDED),
    ]))

    # ── Equity curve ──
    spark = equity_sparkline(equity, width=70)
    eq_text = Text()
    eq_text.append(f"  ${equity[0]:,.2f}", style="dim")
    eq_text.append("  →  ")
    eq_text.append(f"${equity[-1]:,.2f}", style="green" if equity[-1] >= equity[0] else "red")
    eq_text.append(f"   ({len(equity)} bars)\n\n  ", style="dim")
    eq_text.append(spark, style="green" if equity[-1] >= equity[0] else "red")
    console.print(Panel(eq_text, title="[bold]Equity Curve[/]", box=box.ROUNDED))


def render_trade_log(trades: list[BTrade], df: pd.DataFrame) -> None:
    tbl = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow", expand=True)
    tbl.add_column("#", width=4)
    tbl.add_column("Dir", width=5)
    tbl.add_column("Entry", justify="right", min_width=9)
    tbl.add_column("Exit", justify="right", min_width=9)
    tbl.add_column("SL", justify="right", min_width=9)
    tbl.add_column("TP", justify="right", min_width=9)
    tbl.add_column("Bars", justify="right", width=5)
    tbl.add_column("Reason", width=8)
    tbl.add_column("P&L", justify="right", min_width=10)
    tbl.add_column("R", justify="right", width=6)

    for n, t in enumerate(trades, 1):
        dir_style = "green" if t.direction == "LONG" else "red"
        pnl_style = "green" if (t.pnl or 0) > 0 else "red"
        reason_style = {"TP": "green", "SL": "red", "SIGNAL": "yellow", "EOD": "dim"}.get(
            t.exit_reason or "", "white"
        )
        tbl.add_row(
            str(n),
            Text(t.direction[0], style=dir_style),
            f"{t.entry_price:,.2f}",
            f"{t.exit_price:,.2f}" if t.exit_price else "—",
            f"{t.stop_loss:,.2f}",
            f"{t.take_profit:,.2f}",
            str(t.bars_held or "—"),
            Text(t.exit_reason or "—", style=reason_style),
            Text(f"${t.pnl:+,.2f}" if t.pnl is not None else "—", style=pnl_style),
            Text(f"{t.r_multiple:.1f}R" if t.r_multiple is not None else "—",
                 style=pnl_style),
        )

    console.print(Panel(tbl, title="[bold yellow]Trade Log[/]", box=box.ROUNDED))


def render_exit_breakdown(trades: list[BTrade]) -> None:
    reasons = {"SL": 0, "TP": 0, "SIGNAL": 0, "EOD": 0}
    for t in trades:
        if t.exit_reason in reasons:
            reasons[t.exit_reason] += 1

    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold", expand=False)
    tbl.add_column("Exit reason")
    tbl.add_column("Count", justify="right")
    tbl.add_column("Bar", justify="center")

    total = len(trades)
    styles = {"TP": "green", "SL": "red", "SIGNAL": "yellow", "EOD": "dim"}
    bars_map = {"TP": "█" * 20, "SL": "█" * 20, "SIGNAL": "█" * 20, "EOD": "█" * 20}

    for reason, count in reasons.items():
        pct = count / total * 20 if total else 0
        bar = bars_map[reason][:int(pct)]
        tbl.add_row(
            Text(reason, style=styles[reason]),
            str(count),
            Text(bar or "·", style=styles[reason]),
        )

    console.print(Panel(tbl, title="[bold]Exit Breakdown[/]", box=box.ROUNDED))


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    live_mode = "--live" in args

    n_bars = 2000
    if "--bars" in args:
        idx = args.index("--bars")
        n_bars = int(args[idx + 1])

    seed = None
    if "--seed" in args:
        idx = args.index("--seed")
        seed = int(args[idx + 1])

    if live_mode:
        console.print("[bold]Fetching live XAU/USD data from Yahoo Finance…[/]")
        raw = mdata.fetch_ohlcv()
    else:
        console.print(f"[bold]Generating {n_bars} synthetic XAU/USD bars…[/]")
        if seed is not None:
            np.random.seed(seed)
        else:
            np.random.seed(42)  # fixed seed for reproducible demo
        raw = mdata._generate_demo_ohlcv(n_bars)

    console.print(f"  Data: {len(raw)} raw bars  [{raw.index[0].strftime('%Y-%m-%d %H:%M')} → {raw.index[-1].strftime('%Y-%m-%d %H:%M')}]")
    df = mdata.add_indicators(raw)
    console.print(f"  After indicators: {len(df)} bars")

    console.print("\n[bold]Running walk-forward backtest…[/]")
    trades, equity = run_backtest(df)
    console.print(f"  {len(trades)} trades executed\n")

    if not trades:
        console.print("[yellow]No trades generated — strategy produced no signals on this data.[/]")
        return

    metrics = compute_metrics(trades, equity, len(df))

    render_summary(metrics, equity, df)
    render_exit_breakdown(trades)

    # Show full trade log only if ≤ 50 trades; else show last 20
    if len(trades) <= 50:
        render_trade_log(trades, df)
    else:
        console.print(f"\n[dim](Showing last 20 of {len(trades)} trades)[/]")
        render_trade_log(trades[-20:], df)


if __name__ == "__main__":
    main()
