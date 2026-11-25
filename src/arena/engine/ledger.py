"""Agent virtual ledger manager: JSON state persistence, fill accounting, MTM, circuit breakers, and journals."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import (
    AGENT_SPECS,
    AGENT_SPECS_BY_ID,
    AGENTS_STATE_DIR,
    DAILY_LOSS_LIMIT_PCT,
    INITIAL_CASH,
    MAX_TOTAL_DRAWDOWN_PCT,
    AgentSpec,
)
from .simulator import FillSimulator
from ..models import (
    AgentLedgerState,
    Bar,
    DailyJournalEntry,
    EquityPoint,
    OrderSignal,
    Position,
    Quote,
    TradeRecord,
)

try:
    ET_ZONE = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    ET_ZONE = timezone(timedelta(hours=-5), name="EST")


def _timestamp_to_et_date(ts: str) -> str:
    """Convert an ISO-8601 UTC timestamp string to YYYY-MM-DD in America/New_York."""
    if not ts:
        return ""
    try:
        cleaned = ts.strip()
        if len(cleaned) == 10 and cleaned[4] == "-" and cleaned[7] == "-":
            return cleaned
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET_ZONE).strftime("%Y-%m-%d")
    except Exception:
        return ts[:10]


def compute_leaderboard_metrics(state: AgentLedgerState, spy_return_pct: float = 0.0) -> Dict[str, Any]:
    """Compute standardized leaderboard and performance metrics for an agent."""
    initial = state.initial_cash if state.initial_cash > 0 else INITIAL_CASH
    equity = round(state.equity, 2)
    profit_dollars = round(equity - initial, 2)
    profit_pct = round(((equity - initial) / initial) * 100.0, 2)
    return_vs_spy_pct = round(profit_pct - spy_return_pct, 2)

    closed_trades = [t for t in state.trades if t.side == "SELL" and t.realized_pnl is not None]
    closed_count = len(closed_trades)
    wins = [float(t.realized_pnl) for t in closed_trades if (t.realized_pnl or 0.0) > 0.0]
    losses = [float(t.realized_pnl) for t in closed_trades if (t.realized_pnl or 0.0) < 0.0]

    win_rate_pct = round((len(wins) / closed_count) * 100.0, 2) if closed_count > 0 else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 2)
    elif gross_profit > 0:
        profit_factor = round(gross_profit, 2)
    else:
        profit_factor = 0.0

    avg_win_dollars = round(gross_profit / len(wins), 2) if wins else 0.0
    avg_loss_dollars = round(sum(losses) / len(losses), 2) if losses else 0.0

    return {
        "agent_id": state.agent_id,
        "name": state.name,
        "category": state.category,
        "equity": equity,
        "cash": round(state.cash, 2),
        "profit_dollars": profit_dollars,
        "profit_pct": profit_pct,
        "return_vs_spy_pct": return_vs_spy_pct,
        "max_drawdown_pct": round(state.max_drawdown_pct, 2),
        "current_drawdown_pct": round(state.current_drawdown_pct, 2),
        "daily_pnl_pct": round(state.daily_pnl_pct, 2),
        "trades_count": len(state.trades),
        "closed_trades_count": closed_count,
        "open_positions_count": len(state.positions),
        "rejections_count": len(state.rejections),
        "win_rate_pct": win_rate_pct,
        "profit_factor": profit_factor,
        "avg_win_dollars": avg_win_dollars,
        "avg_loss_dollars": avg_loss_dollars,
        "status": state.status,
        "status_reason": state.status_reason,
    }


class LedgerManager:
    """Manages agent virtual ledgers, mark-to-market accounting, circuit breakers, and daily journals."""

    def __init__(self, state_dir: Optional[Union[Path, str]] = None) -> None:
        self.state_dir = Path(state_dir) if state_dir is not None else AGENTS_STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.daily_loss_limit_pct_100 = DAILY_LOSS_LIMIT_PCT * 100.0
        self.max_total_drawdown_pct_100 = MAX_TOTAL_DRAWDOWN_PCT * 100.0

    def get_agent_state_path(self, agent_id: str) -> Path:
        return self.state_dir / f"{agent_id}.json"

    def initialize_agent(
        self,
        spec_or_id: Union[AgentSpec, str],
        initial_cash: float = INITIAL_CASH,
        timestamp: str = "",
        trading_date_et: str = "",
        save: bool = True,
    ) -> AgentLedgerState:
        """Create a fresh AgentLedgerState for the given AgentSpec or agent_id."""
        if isinstance(spec_or_id, str):
            spec = AGENT_SPECS_BY_ID.get(
                spec_or_id,
                AgentSpec(
                    agent_id=spec_or_id,
                    name=spec_or_id,
                    category="Coded Strategy",
                    short_description="",
                    playbook="",
                ),
            )
        else:
            spec = spec_or_id

        state = AgentLedgerState(
            agent_id=spec.agent_id,
            name=spec.name,
            category=spec.category,
            status="ACTIVE",
            status_reason="Trading active",
            initial_cash=round(initial_cash, 2),
            cash=round(initial_cash, 2),
            equity=round(initial_cash, 2),
            peak_equity=round(initial_cash, 2),
            max_drawdown_pct=0.0,
            current_drawdown_pct=0.0,
            current_trading_date_et=trading_date_et,
            day_start_equity=round(initial_cash, 2),
            daily_pnl_pct=0.0,
            last_updated=timestamp,
        )
        if save:
            self.save_agent_state(state)
        return state

    def load_agent_state(self, agent_id: str) -> AgentLedgerState:
        """Load an AgentLedgerState from JSON disk storage."""
        path = self.get_agent_state_path(agent_id)
        if not path.exists():
            raise FileNotFoundError(f"Agent state file not found: {path}")
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return AgentLedgerState.from_dict(data)

    def load_or_initialize_agent(
        self,
        spec_or_id: Union[AgentSpec, str],
        initial_cash: float = INITIAL_CASH,
    ) -> AgentLedgerState:
        """Load existing agent ledger state from disk or initialize a new one if absent."""
        agent_id = spec_or_id if isinstance(spec_or_id, str) else spec_or_id.agent_id
        path = self.get_agent_state_path(agent_id)
        if path.exists():
            return self.load_agent_state(agent_id)
        return self.initialize_agent(spec_or_id, initial_cash=initial_cash, save=True)

    def save_agent_state(self, state: AgentLedgerState) -> Path:
        """Persist AgentLedgerState to state/agents/<agent_id>.json."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.get_agent_state_path(state.agent_id)
        with path.open("w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2)
        return path

    def initialize_all_agents(
        self,
        initial_cash: float = INITIAL_CASH,
        overwrite: bool = False,
    ) -> Dict[str, AgentLedgerState]:
        """Ensure all 10 competition agents have initialized ledger states."""
        states: Dict[str, AgentLedgerState] = {}
        for spec in AGENT_SPECS:
            path = self.get_agent_state_path(spec.agent_id)
            if path.exists() and not overwrite:
                states[spec.agent_id] = self.load_agent_state(spec.agent_id)
            else:
                states[spec.agent_id] = self.initialize_agent(spec, initial_cash=initial_cash, save=True)
        return states

    def start_of_day_check(self, state: AgentLedgerState, trading_date_et: str) -> bool:
        """Handle rollover to a new US trading day: reset DAILY_HALTED to ACTIVE and record day_start_equity."""
        if not trading_date_et or state.current_trading_date_et == trading_date_et:
            return False

        state.current_trading_date_et = trading_date_et
        state.day_start_equity = round(state.equity, 2)
        state.daily_pnl_pct = 0.0

        # Reset daily halt on new trading day; never reset BLOWN_UP or COMPLETED
        if state.status == "DAILY_HALTED":
            state.status = "ACTIVE"
            state.status_reason = "Trading active (new trading day reset)"

        return True

    def apply_trade(self, state: AgentLedgerState, trade: TradeRecord) -> TradeRecord:
        """Apply a BUY or SELL TradeRecord fill to cash, positions, and trade history (idempotent by trade_id)."""
        if any(existing.trade_id == trade.trade_id for existing in state.trades):
            return trade

        side = trade.side.upper()
        ticker = trade.ticker.upper()

        if side == "BUY":
            total_cost = round(trade.notional_dollars + trade.commission_dollars, 2)
            state.cash = round(max(0.0, state.cash - total_cost), 2)

            if ticker in state.positions and state.positions[ticker].shares > 0:
                pos = state.positions[ticker]
                old_shares = pos.shares
                new_shares = round(old_shares + trade.shares, 4)
                weighted_avg = ((old_shares * pos.avg_entry_price) + (trade.shares * trade.fill_price)) / new_shares
                pos.shares = new_shares
                pos.avg_entry_price = round(weighted_avg, 4)
                if trade.stop_loss is not None and trade.stop_loss > 0:
                    pos.stop_loss = float(trade.stop_loss)
                if trade.take_profit is not None and trade.take_profit > 0:
                    pos.take_profit = float(trade.take_profit)
                if trade.reasoning:
                    pos.entry_reasoning = trade.reasoning
                pos.update_mark(trade.fill_price)
            else:
                pos = Position(
                    ticker=ticker,
                    shares=round(trade.shares, 4),
                    avg_entry_price=round(trade.fill_price, 4),
                    current_price=round(trade.fill_price, 4),
                    stop_loss=float(trade.stop_loss or 0.0),
                    take_profit=float(trade.take_profit) if trade.take_profit is not None else None,
                    opened_at=trade.fill_timestamp or trade.decision_timestamp,
                    entry_reasoning=trade.reasoning,
                )
                pos.update_mark(trade.fill_price)
                state.positions[ticker] = pos

            state.trades.append(trade)
            if trade.fill_timestamp:
                state.last_updated = trade.fill_timestamp
            return trade

        if side == "SELL":
            if ticker not in state.positions or state.positions[ticker].shares <= 0:
                raise ValueError(f"Cannot apply SELL trade for '{ticker}': no open position in ledger")

            pos = state.positions[ticker]
            sell_shares = round(min(trade.shares, pos.shares), 4)
            notional = round(sell_shares * trade.fill_price, 2)
            proceeds = round(notional - trade.commission_dollars, 2)
            cost_basis = sell_shares * pos.avg_entry_price
            realized_pnl = round(proceeds - cost_basis, 2)
            realized_pnl_pct = (
                round(((trade.fill_price - pos.avg_entry_price) / pos.avg_entry_price) * 100.0, 2)
                if pos.avg_entry_price > 0
                else 0.0
            )

            trade.shares = sell_shares
            trade.notional_dollars = notional
            trade.realized_pnl = realized_pnl
            trade.realized_pnl_pct = realized_pnl_pct
            if not trade.exit_reason:
                trade.exit_reason = "AGENT_SIGNAL"

            state.cash = round(state.cash + proceeds, 2)
            remaining_shares = round(pos.shares - sell_shares, 4)
            if remaining_shares <= 1e-6:
                state.positions.pop(ticker, None)
            else:
                pos.shares = remaining_shares
                pos.update_mark(trade.fill_price)

            state.trades.append(trade)
            if trade.fill_timestamp:
                state.last_updated = trade.fill_timestamp
            return trade

        raise ValueError(f"Unsupported trade side: {side}")

    @staticmethod
    def _extract_mark_price(val: Any) -> float:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, Quote):
            return val.mid_price if val.mid_price > 0 else max(val.bid_price, val.ask_price, 0.0)
        if isinstance(val, Bar):
            return val.close if val.close > 0 else val.open
        return 0.0

    def update_mark_to_market(
        self,
        state: AgentLedgerState,
        prices_by_ticker: Dict[str, Any],
        timestamp: str,
        trading_date_et: str = "",
    ) -> AgentLedgerState:
        """Revalue open positions, update equity/drawdown/daily PnL, enforce circuit breakers, and append EquityPoint."""
        eff_date_et = trading_date_et or state.current_trading_date_et or _timestamp_to_et_date(timestamp)
        if eff_date_et and not state.current_trading_date_et:
            state.current_trading_date_et = eff_date_et
            state.day_start_equity = round(state.equity, 2)

        for ticker, pos in state.positions.items():
            raw_price = prices_by_ticker.get(ticker) if prices_by_ticker else None
            price = self._extract_mark_price(raw_price) if raw_price is not None else pos.current_price
            if price <= 0:
                price = pos.current_price if pos.current_price > 0 else pos.avg_entry_price
            pos.update_mark(price)

        positions_value = round(sum(p.market_value for p in state.positions.values()), 2)
        state.equity = round(state.cash + positions_value, 2)
        state.peak_equity = round(max(state.peak_equity, state.equity), 2)

        if state.peak_equity > 0:
            state.current_drawdown_pct = round(
                ((state.equity - state.peak_equity) / state.peak_equity) * 100.0, 4
            )
        else:
            state.current_drawdown_pct = 0.0

        state.max_drawdown_pct = round(min(state.max_drawdown_pct, state.current_drawdown_pct), 4)

        if state.day_start_equity > 0:
            state.daily_pnl_pct = round(
                ((state.equity - state.day_start_equity) / state.day_start_equity) * 100.0, 4
            )
        else:
            state.daily_pnl_pct = 0.0

        # Circuit breakers
        if state.current_drawdown_pct <= self.max_total_drawdown_pct_100 + 1e-9:
            state.status = "BLOWN_UP"
            state.status_reason = "Total drawdown reached -10% limit (blown up)"
        elif state.daily_pnl_pct <= self.daily_loss_limit_pct_100 + 1e-9 and state.status == "ACTIVE":
            state.status = "DAILY_HALTED"
            state.status_reason = "Daily loss limit -3% reached"

        point = EquityPoint(
            timestamp=timestamp,
            trading_date_et=eff_date_et,
            equity=round(state.equity, 2),
            cash=round(state.cash, 2),
            positions_value=positions_value,
            drawdown_pct=round(state.current_drawdown_pct, 2),
            daily_return_pct=round(state.daily_pnl_pct, 2),
        )
        if state.equity_curve and state.equity_curve[-1].timestamp == timestamp:
            state.equity_curve[-1] = point
        else:
            state.equity_curve.append(point)

        if timestamp:
            state.last_updated = timestamp

        return state

    def flatten_all_positions(
        self,
        state: AgentLedgerState,
        quotes_by_ticker: Dict[str, Any],
        decision_timestamp: str,
        fill_timestamp: str,
        exit_reason: str = "EOD_FLAT",
        simulator: Optional[FillSimulator] = None,
    ) -> List[TradeRecord]:
        """Liquidate all open positions (for ORB EOD flat, BLOWN_UP liquidation, or Day-10 competition end)."""
        sim = simulator or FillSimulator()
        executed_trades: List[TradeRecord] = []

        for ticker, pos in list(state.positions.items()):
            if pos.shares <= 0:
                continue

            quote_or_price = (
                quotes_by_ticker.get(ticker)
                if (quotes_by_ticker and ticker in quotes_by_ticker)
                else (pos.current_price if pos.current_price > 0 else pos.avg_entry_price)
            )

            reason_map = {
                "EOD_FLAT": f"End-of-day mandatory flatten for {ticker} ({pos.shares:.4f} shares)",
                "EOD_FLAT_CATCHUP": f"Catch-up flatten for intraday-only position in {ticker} ({pos.shares:.4f} shares)",
                "BLOWN_UP": f"Mandatory liquidation of {ticker} after breaching -10% max drawdown limit",
                "COMPETITION_END": f"Final-day competition close mark/flatten for {ticker}",
            }
            reasoning = reason_map.get(
                exit_reason,
                f"Flattening open position in {ticker} ({exit_reason})",
            )

            sell_signal = OrderSignal(
                agent_id=state.agent_id,
                action="SELL",
                ticker=ticker,
                stop_loss=pos.stop_loss,
                take_profit=pos.take_profit,
                confidence=1.0,
                reasoning=reasoning,
                requested_shares=pos.shares,
                decision_timestamp=decision_timestamp,
            )

            trade = sim.simulate_fill(
                order_or_check=sell_signal,
                fill_quote_or_bar=quote_or_price,
                fill_timestamp=fill_timestamp,
                shares=pos.shares,
                state=state,
                exit_reason=exit_reason,
                decision_timestamp=decision_timestamp,
            )
            self.apply_trade(state, trade)
            executed_trades.append(trade)

        return executed_trades

    def update_daily_journal(
        self,
        state: AgentLedgerState,
        trading_date_et: str,
        trading_day_number: int,
    ) -> DailyJournalEntry:
        """Create or update a plain-English DailyJournalEntry summarizing the agent's actions on trading_date_et."""
        day_trades = [
            t
            for t in state.trades
            if _timestamp_to_et_date(t.fill_timestamp or t.decision_timestamp) == trading_date_et
        ]
        day_rejections = [
            r for r in state.rejections if _timestamp_to_et_date(r.timestamp) == trading_date_et
        ]

        start_equity = round(state.day_start_equity, 2)
        end_equity = round(state.equity, 2)
        daily_pnl = round(end_equity - start_equity, 2)
        daily_pnl_pct = (
            round(((end_equity - start_equity) / start_equity) * 100.0, 2) if start_equity > 0 else 0.0
        )

        buys = [t for t in day_trades if t.side == "BUY"]
        sells = [t for t in day_trades if t.side == "SELL"]
        highlights: List[str] = []

        for b in buys:
            hl = (
                f"BUY {b.shares:.4f} {b.ticker} @ ${b.fill_price:.2f} (${b.notional_dollars:.2f}, "
                f"stop ${b.stop_loss:.2f})"
                if b.stop_loss
                else f"BUY {b.shares:.4f} {b.ticker} @ ${b.fill_price:.2f} (${b.notional_dollars:.2f})"
            )
            if b.reasoning:
                hl += f" — {b.reasoning}"
            highlights.append(hl)

        for s in sells:
            pnl_str = (
                f"{'+' if (s.realized_pnl or 0) >= 0 else ''}${s.realized_pnl:.2f} ({s.realized_pnl_pct:+.2f}%)"
                if s.realized_pnl is not None and s.realized_pnl_pct is not None
                else "closed"
            )
            hl = f"SELL {s.shares:.4f} {s.ticker} @ ${s.fill_price:.2f} [{s.exit_reason or 'EXIT'}: {pnl_str}]"
            if s.reasoning:
                hl += f" — {s.reasoning}"
            highlights.append(hl)

        for r in day_rejections:
            highlights.append(f"REJECTED {r.action} {r.ticker}: {r.reason}")

        parts: List[str] = []
        pnl_sign = "+" if daily_pnl >= 0 else ""
        parts.append(
            f"Day {trading_day_number} ({trading_date_et}): Equity moved from ${start_equity:,.2f} to "
            f"${end_equity:,.2f} ({pnl_sign}${daily_pnl:,.2f}, {daily_pnl_pct:+.2f}%)."
        )

        if buys or sells:
            buy_tickers = ", ".join(f"{b.ticker} (${b.notional_dollars:,.0f})" for b in buys) or "none"
            sell_tickers = (
                ", ".join(
                    f"{s.ticker} ({s.exit_reason or 'SIGNAL'}, "
                    f"{'+' if (s.realized_pnl or 0) >= 0 else ''}${(s.realized_pnl or 0.0):.2f})"
                    for s in sells
                )
                or "none"
            )
            parts.append(f"Executed {len(day_trades)} trade(s) — Buys: {buy_tickers}; Sells: {sell_tickers}.")
        else:
            if state.positions:
                held = ", ".join(
                    f"{t} ({p.shares:.2f} sh, {p.unrealized_pnl_pct:+.2f}%)"
                    for t, p in state.positions.items()
                )
                parts.append(f"No new trades executed; held existing position(s): {held}.")
            else:
                parts.append("Stayed in 100% cash with no trades executed.")

        if day_rejections:
            parts.append(
                f"Risk Manager blocked {len(day_rejections)} order(s) "
                f"({'; '.join(f'{r.ticker}: {r.reason}' for r in day_rejections[:2])})."
            )

        if state.status != "ACTIVE":
            parts.append(f"Status at close: {state.status} ({state.status_reason}).")

        entry = DailyJournalEntry(
            trading_date_et=trading_date_et,
            trading_day_number=trading_day_number,
            start_equity=start_equity,
            end_equity=end_equity,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            trades_count=len(day_trades),
            rejections_count=len(day_rejections),
            summary_text=" ".join(parts),
            highlights=highlights,
        )

        replaced = False
        for idx, existing in enumerate(state.daily_journal):
            if existing.trading_date_et == trading_date_et:
                state.daily_journal[idx] = entry
                replaced = True
                break
        if not replaced:
            state.daily_journal.append(entry)

        return entry

    @staticmethod
    def compute_leaderboard_metrics(state: AgentLedgerState, spy_return_pct: float = 0.0) -> Dict[str, Any]:
        return compute_leaderboard_metrics(state, spy_return_pct=spy_return_pct)

    # Aliases for caller convenience
    record_trade = apply_trade
    apply_fill = apply_trade
