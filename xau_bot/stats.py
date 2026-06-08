#!/usr/bin/env python3
"""Daily stats viewer — run any time to see current trading performance.

Usage:
  python stats.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "xau_bot.log"

console = Console()


def _spark(history: list[float]) -> str:
    if len(history) < 2:
        return "Not enough data yet"
    hi, lo = max(history), min(history)
    spread = hi - lo or 1
    bars = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    return "".join(bars[int((v - lo) / spread * 7)] for v in history[-60:])


def _pnl_text(v: float) -> Text:
    color = "green" if v >= 0 else "red"
    return Text(f"${v:+,.2f}", style=color)


def _bot_status(last_updated: str) -> tuple[str, str]:
    try:
        upd = datetime.fromisoformat(last_updated)
        age = (datetime.utcnow() - upd).total_seconds()
        upd_str = upd.strftime("%Y-%m-%d %H:%M UTC")
        if age < 90:
            return "[bold green]● RUNNING[/]", upd_str
        if age < 600:
            return "[bold yellow]● IDLE[/]", upd_str
        return "[bold red]● STOPPED[/]", upd_str
    except Exception:
        return "[dim]Unknown[/]", last_updated


def main():
    if not STATE_FILE.exists():
        console.print(Panel(
            "[red]No state file found.[/]\n\n"
            "The bot hasn't run yet. Start it with:\n\n"
            "  [bold]bash setup.sh[/]  (first time)\n"
            "  [bold]./start_bot.sh[/] (after setup)",
            title="XAU/USD Stats", box=box.ROUNDED,
        ))
        sys.exit(0)

    state = json.loads(STATE_FILE.read_text())

    balance = state.get("balance", 10_000)
    trade_count = state.get("trade_count", 0)
    closed = state.get("closed_trades", [])
    position = state.get("position")
    equity = state.get("equity_history", [])
    last_updated = state.get("last_updated", "")

    from config import STARTING_BALANCE
    total_pnl = balance - STARTING_BALANCE
    ret_pct = total_pnl / STARTING_BALANCE * 100
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0.0

    status_label, upd_str = _bot_status(last_updated)

    console.print()
    console.rule("[bold gold1]  XAU/USD Paper Trader — Daily Stats  [/]")
    console.print(f"\n  Bot: {status_label}   Last tick: [dim]{upd_str}[/]\n")

    # ── Account summary ──
    acct = Table(box=box.SIMPLE, show_header=False, expand=False)
    acct.add_column("Label", style="dim", min_width=18)
    acct.add_column("Value", min_width=14)

    acct.add_row("Starting Balance", f"[white]${STARTING_BALANCE:,.2f}[/]")
    acct.add_row("Current Balance", Text(f"${balance:,.2f}", style="bold white"))
    acct.add_row("Total P&L", _pnl_text(total_pnl))
    pct_col = "green" if ret_pct >= 0 else "red"
    acct.add_row("Return", Text(f"{ret_pct:+.2f}%", style=pct_col))
    acct.add_row("Total Trades", str(trade_count))
    acct.add_row("Win Rate", f"{win_rate:.1f}%")

    console.print(Panel(acct, title="[bold cyan]Account[/]", box=box.ROUNDED))

    # ── Open position ──
    if position:
        pos_tbl = Table(box=box.SIMPLE, show_header=False)
        pos_tbl.add_column("Label", style="dim", min_width=14)
        pos_tbl.add_column("Value")
        dir_style = "bold green" if position["direction"] == "LONG" else "bold red"
        pos_tbl.add_row("Direction", Text(position["direction"], style=dir_style))
        pos_tbl.add_row("Entry", f"{position['entry_price']:,.2f}")
        pos_tbl.add_row("Stop Loss", Text(f"{position['stop_loss']:,.2f}", style="red"))
        pos_tbl.add_row("Take Profit", Text(f"{position['take_profit']:,.2f}", style="green"))
        console.print(Panel(pos_tbl, title="[bold yellow]Open Position[/]", box=box.ROUNDED))
    else:
        console.print(Panel("[dim]No open position[/]", title="[bold yellow]Open Position[/]", box=box.ROUNDED))

    # ── Equity curve ──
    eq_data = equity if equity else [STARTING_BALANCE, balance]
    spark = _spark(eq_data)
    hi, lo = max(eq_data), min(eq_data)
    spark_text = Text()
    spark_text.append(f"Hi: ${hi:,.2f}   Lo: ${lo:,.2f}\n", style="dim")
    curve_color = "green" if total_pnl >= 0 else "red"
    spark_text.append(spark + "\n", style=curve_color)
    spark_text.append("  ←── equity curve (last 60 trades) ───►", style="dim")
    console.print(Panel(spark_text, title="[bold]Equity Curve[/]", box=box.ROUNDED))

    # ── Recent trades ──
    if closed:
        tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold magenta", expand=True)
        tbl.add_column("Closed", style="dim", width=14)
        tbl.add_column("Dir", width=6)
        tbl.add_column("Entry", justify="right", min_width=9)
        tbl.add_column("Exit", justify="right", min_width=9)
        tbl.add_column("P&L", justify="right", min_width=10)

        for t in reversed(closed[-10:]):
            dir_style = "green" if t["direction"] == "LONG" else "red"
            pnl = t.get("pnl") or 0
            time_str = ""
            if t.get("exit_time"):
                try:
                    time_str = datetime.fromisoformat(t["exit_time"]).strftime("%m-%d %H:%M")
                except Exception:
                    pass
            tbl.add_row(
                time_str,
                Text(t["direction"][0], style=dir_style),
                f"{t['entry_price']:,.2f}",
                f"{t['exit_price']:,.2f}" if t.get("exit_price") else "—",
                _pnl_text(pnl),
            )
        console.print(Panel(tbl, title="[bold magenta]Recent Trades (last 10)[/]", box=box.ROUNDED))

    # ── Log tail ──
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text().splitlines()
        tail = lines[-6:] if len(lines) >= 6 else lines
        log_text = Text("\n".join(tail), style="dim")
        console.print(Panel(log_text, title="[dim]Recent Log[/]", box=box.ROUNDED))

    console.print()


if __name__ == "__main__":
    main()
