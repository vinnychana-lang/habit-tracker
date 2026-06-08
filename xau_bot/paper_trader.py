from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from config import STARTING_BALANCE, POSITION_SIZE_PCT, STOP_LOSS_ATR_MULT, TAKE_PROFIT_ATR_MULT


@dataclass
class Trade:
    direction: str          # "LONG" or "SHORT"
    entry_price: float
    quantity: float         # oz of gold
    stop_loss: float
    take_profit: float
    entry_time: datetime = field(default_factory=datetime.utcnow)
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    pnl: Optional[float] = None

    def close(self, price: float) -> float:
        self.exit_price = price
        self.exit_time = datetime.utcnow()
        if self.direction == "LONG":
            self.pnl = (price - self.entry_price) * self.quantity
        else:
            self.pnl = (self.entry_price - price) * self.quantity
        return self.pnl


class PaperTrader:
    def __init__(self):
        self.balance = STARTING_BALANCE
        self.position: Optional[Trade] = None
        self.closed_trades: list[Trade] = []
        self.trade_count = 0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.closed_trades if t.pnl is not None)

    @property
    def win_rate(self) -> float:
        wins = [t for t in self.closed_trades if t.pnl and t.pnl > 0]
        if not self.closed_trades:
            return 0.0
        return len(wins) / len(self.closed_trades) * 100

    @property
    def unrealized_pnl(self) -> Optional[float]:
        return None

    def open_long(self, price: float, atr: float) -> Optional[Trade]:
        if self.position:
            return None
        capital = self.balance * POSITION_SIZE_PCT
        qty = capital / price
        sl = price - atr * STOP_LOSS_ATR_MULT
        tp = price + atr * TAKE_PROFIT_ATR_MULT
        self.position = Trade("LONG", price, qty, sl, tp)
        self.trade_count += 1
        return self.position

    def open_short(self, price: float, atr: float) -> Optional[Trade]:
        if self.position:
            return None
        capital = self.balance * POSITION_SIZE_PCT
        qty = capital / price
        sl = price + atr * STOP_LOSS_ATR_MULT
        tp = price - atr * TAKE_PROFIT_ATR_MULT
        self.position = Trade("SHORT", price, qty, sl, tp)
        self.trade_count += 1
        return self.position

    def check_exits(self, high: float, low: float) -> Optional[Trade]:
        """Check if current bar hit SL or TP. Returns closed trade or None."""
        if not self.position:
            return None
        p = self.position
        hit_price = None

        if p.direction == "LONG":
            if low <= p.stop_loss:
                hit_price = p.stop_loss
            elif high >= p.take_profit:
                hit_price = p.take_profit
        else:
            if high >= p.stop_loss:
                hit_price = p.stop_loss
            elif low <= p.take_profit:
                hit_price = p.take_profit

        if hit_price is not None:
            pnl = p.close(hit_price)
            self.balance += pnl
            self.closed_trades.append(p)
            self.position = None
            return p
        return None

    def close_position(self, price: float) -> Optional[Trade]:
        if not self.position:
            return None
        pnl = self.position.close(price)
        self.balance += pnl
        self.closed_trades.append(self.position)
        closed = self.position
        self.position = None
        return closed

    def unrealized(self, current_price: float) -> float:
        if not self.position:
            return 0.0
        p = self.position
        if p.direction == "LONG":
            return (current_price - p.entry_price) * p.quantity
        return (p.entry_price - current_price) * p.quantity
