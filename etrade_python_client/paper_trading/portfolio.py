"""
Paper-trading portfolio tracker.

Simulates a brokerage account with cash and stock positions.
No real orders are placed — everything is tracked in memory and
persisted to a local JSON file so state survives restarts.
"""

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger("my_logger")

DEFAULT_STATE_FILE = "paper_portfolio.json"


class PaperPortfolio:
    """Simulated portfolio for paper trading."""

    def __init__(
        self,
        starting_cash: float = 100_000.00,
        state_file: str = DEFAULT_STATE_FILE,
        seed_positions: dict[str, dict] | None = None,
    ):
        self.state_file = state_file
        self.cash = starting_cash
        self.positions: dict[str, dict] = {}  # symbol -> {qty, avg_cost}
        self.trade_log: list[dict] = []

        loaded = self._load_state()

        # Seed positions only on first run (no existing state file)
        if not loaded and seed_positions:
            for symbol, pos in seed_positions.items():
                self.positions[symbol] = {
                    "qty": pos["qty"],
                    "avg_cost": pos["avg_cost"],
                }
            logger.info(
                "Seeded portfolio with %d position(s): %s",
                len(seed_positions),
                ", ".join(seed_positions.keys()),
            )
            self._save_state()

    # ------------------------------------------------------------------ #
    #  Core actions
    # ------------------------------------------------------------------ #

    def buy(self, symbol: str, qty: int, price: float) -> bool:
        """
        Simulate buying shares.
        Returns True if the order was filled, False if insufficient cash.
        """
        cost = qty * price
        if cost > self.cash:
            logger.warning(
                "PAPER BUY rejected — insufficient cash. Need $%.2f, have $%.2f",
                cost, self.cash,
            )
            return False

        self.cash -= cost

        if symbol in self.positions:
            pos = self.positions[symbol]
            total_qty = pos["qty"] + qty
            pos["avg_cost"] = ((pos["avg_cost"] * pos["qty"]) + cost) / total_qty
            pos["qty"] = total_qty
        else:
            self.positions[symbol] = {"qty": qty, "avg_cost": price}

        self._record_trade("BUY", symbol, qty, price)
        self._save_state()
        logger.info("PAPER BUY  %d x %s @ $%.2f  (cash remaining: $%.2f)", qty, symbol, price, self.cash)
        return True

    def sell(self, symbol: str, qty: int, price: float) -> bool:
        """
        Simulate selling shares.
        Returns True if the order was filled, False if insufficient shares.
        """
        if symbol not in self.positions or self.positions[symbol]["qty"] < qty:
            held = self.positions.get(symbol, {}).get("qty", 0)
            logger.warning(
                "PAPER SELL rejected — not enough shares of %s. Want %d, have %d",
                symbol, qty, held,
            )
            return False

        revenue = qty * price
        self.cash += revenue
        self.positions[symbol]["qty"] -= qty

        if self.positions[symbol]["qty"] == 0:
            del self.positions[symbol]

        self._record_trade("SELL", symbol, qty, price)
        self._save_state()
        logger.info("PAPER SELL %d x %s @ $%.2f  (cash remaining: $%.2f)", qty, symbol, price, self.cash)
        return True

    # ------------------------------------------------------------------ #
    #  Reporting
    # ------------------------------------------------------------------ #

    def summary(self, current_prices: dict[str, float] | None = None) -> str:
        """Return a human-readable portfolio summary."""
        lines = [
            "=" * 60,
            "  PAPER PORTFOLIO SUMMARY",
            "=" * 60,
            f"  Cash:  ${self.cash:,.2f}",
            "",
        ]
        total_value = self.cash
        if self.positions:
            lines.append("  Positions:")
            for sym, pos in sorted(self.positions.items()):
                mkt_price = (current_prices or {}).get(sym)
                line = f"    {sym:6s}  qty={pos['qty']:>6}  avg_cost=${pos['avg_cost']:>10,.2f}"
                if mkt_price is not None:
                    mkt_val = pos["qty"] * mkt_price
                    pnl = mkt_val - (pos["qty"] * pos["avg_cost"])
                    total_value += mkt_val
                    line += f"  mkt=${mkt_price:>10,.2f}  value=${mkt_val:>12,.2f}  P&L=${pnl:>+10,.2f}"
                else:
                    total_value += pos["qty"] * pos["avg_cost"]
                lines.append(line)
        else:
            lines.append("  Positions: (none)")

        lines.append("")
        lines.append(f"  Total Portfolio Value: ${total_value:,.2f}")
        lines.append("=" * 60)
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Persistence
    # ------------------------------------------------------------------ #

    def _record_trade(self, action: str, symbol: str, qty: int, price: float):
        self.trade_log.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "symbol": symbol,
            "qty": qty,
            "price": price,
            "cash_after": self.cash,
        })

    def _save_state(self):
        state = {
            "cash": self.cash,
            "positions": self.positions,
            "trade_log": self.trade_log,
        }
        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except OSError as exc:
            logger.error("Failed to save paper portfolio state: %s", exc)

    def _load_state(self) -> bool:
        """Load state from file. Returns True if state was loaded, False otherwise."""
        if not os.path.exists(self.state_file):
            return False
        try:
            with open(self.state_file, "r") as f:
                state = json.load(f)
            self.cash = state.get("cash", self.cash)
            self.positions = state.get("positions", {})
            self.trade_log = state.get("trade_log", [])
            logger.info("Loaded paper portfolio state from %s", self.state_file)
            return True
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load paper portfolio state: %s", exc)
            return False
