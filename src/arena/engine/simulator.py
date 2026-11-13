"""Order fill simulator enforcing strict no-look-ahead, bid-ask spread crossing, and 5 bps slippage."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

from src.arena.config import (
    COMMISSION_DOLLARS,
    DEFAULT_SPREAD_BPS,
    SLIPPAGE_RATE,
)
from src.arena.models import (
    AgentLedgerState,
    Bar,
    OrderSignal,
    Quote,
    RiskCheckResult,
    TradeRecord,
)


def _parse_iso_timestamp(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime."""
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def verify_no_lookahead(decision_timestamp: str, fill_timestamp: str) -> None:
    """Assert that fill_timestamp is strictly after decision_timestamp (no look-ahead bias)."""
    if not decision_timestamp or not fill_timestamp:
        raise ValueError("Both decision_timestamp and fill_timestamp must be non-empty ISO-8601 strings.")
    dec_dt = _parse_iso_timestamp(decision_timestamp)
    fill_dt = _parse_iso_timestamp(fill_timestamp)
    if fill_dt <= dec_dt:
        raise ValueError(
            f"LOOK-AHEAD VIOLATION: fill_timestamp ({fill_timestamp}) must be strictly greater than "
            f"decision_timestamp ({decision_timestamp})."
        )


class FillSimulator:
    """Simulates realistic execution fills with bid-ask spread crossing, 5 bps slippage, and intrabar stops."""

    def __init__(
        self,
        slippage_rate: float = SLIPPAGE_RATE,
        default_spread_bps: float = DEFAULT_SPREAD_BPS,
        commission_dollars: float = COMMISSION_DOLLARS,
    ) -> None:
        self.slippage_rate = slippage_rate
        self.default_spread_bps = default_spread_bps
        self.commission_dollars = commission_dollars

    @staticmethod
    def verify_no_lookahead(decision_timestamp: str, fill_timestamp: str) -> None:
        verify_no_lookahead(decision_timestamp, fill_timestamp)

    def resolve_quote(
        self,
        quote_or_bar_or_price: Union[Quote, Bar, float, int],
        ticker: str = "",
        timestamp: str = "",
        spread_bps: Optional[float] = None,
    ) -> Quote:
        """Convert a Quote, Bar, or reference float into a normalized Quote with ask >= mid >= bid > 0."""
        eff_spread_bps = spread_bps if (spread_bps is not None and spread_bps > 0) else self.default_spread_bps
        half_spread_rate = (eff_spread_bps / 2.0) / 10_000.0

        if isinstance(quote_or_bar_or_price, Quote):
            q = quote_or_bar_or_price
            mid = q.mid_price
            if mid <= 0 and q.bid_price > 0 and q.ask_price > 0:
                mid = (q.bid_price + q.ask_price) / 2.0
            elif mid <= 0:
                mid = max(q.ask_price, q.bid_price)
            if mid <= 0:
                raise ValueError(f"Invalid Quote price for '{q.ticker or ticker}'")

            ask = q.ask_price if q.ask_price >= mid else round(mid * (1.0 + half_spread_rate), 4)
            bid = q.bid_price if (0 < q.bid_price <= mid) else round(mid * (1.0 - half_spread_rate), 4)
            # If bid == ask == mid, synthesize fallback spread so orders always cross a realistic spread
            if ask <= mid and bid >= mid:
                ask = round(mid * (1.0 + half_spread_rate), 4)
                bid = round(mid * (1.0 - half_spread_rate), 4)
            computed_bps = round(((ask - bid) / mid) * 10_000.0, 2) if mid > 0 else eff_spread_bps
            return Quote(
                ticker=q.ticker or ticker,
                timestamp=q.timestamp or timestamp,
                bid_price=bid,
                ask_price=ask,
                mid_price=round(mid, 4),
                spread_bps=computed_bps,
            )

        if isinstance(quote_or_bar_or_price, Bar):
            b = quote_or_bar_or_price
            mid = b.open if b.open > 0 else b.close
            if mid <= 0:
                raise ValueError(f"Invalid Bar price for '{b.ticker or ticker}'")
            bid = round(mid * (1.0 - half_spread_rate), 4)
            ask = round(mid * (1.0 + half_spread_rate), 4)
            return Quote(
                ticker=b.ticker or ticker,
                timestamp=b.timestamp or timestamp,
                bid_price=bid,
                ask_price=ask,
                mid_price=round(mid, 4),
                spread_bps=eff_spread_bps,
            )

        mid = float(quote_or_bar_or_price)
        if mid <= 0:
            raise ValueError(f"Reference price must be > 0 (got {mid})")
        bid = round(mid * (1.0 - half_spread_rate), 4)
        ask = round(mid * (1.0 + half_spread_rate), 4)
        return Quote(
            ticker=ticker,
            timestamp=timestamp,
            bid_price=bid,
            ask_price=ask,
            mid_price=round(mid, 4),
            spread_bps=eff_spread_bps,
        )

    def simulate_fill(
        self,
        order_or_check: Union[RiskCheckResult, OrderSignal],
        fill_quote_or_bar: Union[Quote, Bar, float, int],
        fill_timestamp: Optional[str] = None,
        shares: Optional[float] = None,
        state: Optional[AgentLedgerState] = None,
        exit_reason: str = "AGENT_SIGNAL",
        decision_timestamp: Optional[str] = None,
    ) -> TradeRecord:
        """Execute a BUY or SELL order at the next available quote/bar with spread + 5 bps slippage."""
        if isinstance(order_or_check, RiskCheckResult):
            if not order_or_check.approved:
                raise ValueError(f"Cannot simulate fill for unapproved order: {order_or_check.rejection_reason}")
            signal = order_or_check.signal
            exec_shares = float(shares if shares is not None else order_or_check.approved_shares)
        elif isinstance(order_or_check, OrderSignal):
            signal = order_or_check
            exec_shares = float(shares if shares is not None else (signal.requested_shares or 0.0))
        else:
            raise TypeError(f"Expected RiskCheckResult or OrderSignal, got {type(order_or_check)}")

        if exec_shares <= 0:
            raise ValueError(f"Execution shares must be > 0 (got {exec_shares})")

        inferred_fill_ts = fill_timestamp or (
            fill_quote_or_bar.timestamp if hasattr(fill_quote_or_bar, "timestamp") else ""
        )
        dec_ts = decision_timestamp or signal.decision_timestamp

        if dec_ts and inferred_fill_ts:
            self.verify_no_lookahead(dec_ts, inferred_fill_ts)

        quote = self.resolve_quote(
            fill_quote_or_bar,
            ticker=signal.ticker,
            timestamp=inferred_fill_ts,
        )
        mid_price = quote.mid_price
        ask_price = quote.ask_price
        bid_price = quote.bid_price

        side = signal.action.upper()
        if side == "BUY":
            fill_price = round(ask_price * (1.0 + self.slippage_rate), 4)
            spread_cost = round((ask_price - mid_price) * exec_shares, 4)
            slippage_cost = round((fill_price - ask_price) * exec_shares, 4)
            notional_dollars = round(fill_price * exec_shares, 2)
            realized_pnl = None
            realized_pnl_pct = None
            reason_code = ""
        elif side == "SELL":
            fill_price = round(bid_price * (1.0 - self.slippage_rate), 4)
            spread_cost = round((mid_price - bid_price) * exec_shares, 4)
            slippage_cost = round((bid_price - fill_price) * exec_shares, 4)
            notional_dollars = round(fill_price * exec_shares, 2)
            reason_code = exit_reason or "AGENT_SIGNAL"
            realized_pnl = None
            realized_pnl_pct = None
            if state is not None and signal.ticker in state.positions:
                pos = state.positions[signal.ticker]
                cost_basis = exec_shares * pos.avg_entry_price
                realized_pnl = round(notional_dollars - cost_basis - self.commission_dollars, 2)
                realized_pnl_pct = (
                    round(((fill_price - pos.avg_entry_price) / pos.avg_entry_price) * 100.0, 2)
                    if pos.avg_entry_price > 0
                    else 0.0
                )
        else:
            raise ValueError(f"Cannot simulate fill for action '{side}'")

        return TradeRecord(
            trade_id=f"trd_{uuid.uuid4().hex[:12]}",
            agent_id=signal.agent_id or (state.agent_id if state is not None else ""),
            side=side,  # type: ignore[arg-type]
            ticker=signal.ticker,
            shares=round(exec_shares, 4),
            decision_timestamp=dec_ts,
            fill_timestamp=inferred_fill_ts,
            reference_price=round(mid_price, 4),
            fill_price=fill_price,
            spread_cost_dollars=spread_cost,
            slippage_cost_dollars=slippage_cost,
            commission_dollars=self.commission_dollars,
            notional_dollars=notional_dollars,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            realized_pnl=realized_pnl,
            realized_pnl_pct=realized_pnl_pct,
            exit_reason=reason_code,
            reasoning=signal.reasoning,
            confidence=signal.confidence,
        )

    def check_and_execute_stops_and_targets(
        self,
        state: AgentLedgerState,
        next_bars_by_ticker: Union[Dict[str, Bar], Dict[str, List[Bar]], Bar],
        quotes_by_ticker: Optional[Dict[str, Quote]] = None,
        decision_timestamp: Optional[str] = None,
        fill_timestamp: Optional[str] = None,
        apply_to_state: bool = True,
    ) -> List[TradeRecord]:
        """Check intrabar low/high against each open position's stop_loss and take_profit and execute exits."""
        if isinstance(next_bars_by_ticker, Bar):
            bars_map: Dict[str, Bar] = {next_bars_by_ticker.ticker: next_bars_by_ticker}
        else:
            bars_map = {}
            for ticker, val in next_bars_by_ticker.items():
                if isinstance(val, list) and val:
                    bars_map[ticker] = val[-1]
                elif isinstance(val, Bar):
                    bars_map[ticker] = val

        triggered_trades: List[TradeRecord] = []

        for ticker, pos in list(state.positions.items()):
            if pos.shares <= 0 or ticker not in bars_map:
                continue

            next_bar = bars_map[ticker]
            bar_ts = fill_timestamp or next_bar.timestamp
            dec_ts = decision_timestamp or pos.opened_at

            if decision_timestamp and bar_ts:
                self.verify_no_lookahead(decision_timestamp, bar_ts)

            trigger_ref_price: Optional[float] = None
            exit_reason = ""
            reasoning = ""

            if pos.stop_loss is not None and pos.stop_loss > 0 and next_bar.low <= pos.stop_loss:
                trigger_ref_price = min(pos.stop_loss, next_bar.open)
                exit_reason = "STOP_LOSS"
                reasoning = (
                    f"Stop-loss triggered at ${trigger_ref_price:.2f} "
                    f"(intrabar low ${next_bar.low:.2f} <= stop ${pos.stop_loss:.2f})"
                )
            elif pos.take_profit is not None and pos.take_profit > 0 and next_bar.high >= pos.take_profit:
                trigger_ref_price = max(pos.take_profit, next_bar.open)
                exit_reason = "TAKE_PROFIT"
                reasoning = (
                    f"Take-profit triggered at ${trigger_ref_price:.2f} "
                    f"(intrabar high ${next_bar.high:.2f} >= target ${pos.take_profit:.2f})"
                )

            if trigger_ref_price is None:
                continue

            spread_bps = self.default_spread_bps
            if quotes_by_ticker and ticker in quotes_by_ticker and quotes_by_ticker[ticker].spread_bps > 0:
                spread_bps = quotes_by_ticker[ticker].spread_bps

            synthetic_quote = self.resolve_quote(
                trigger_ref_price,
                ticker=ticker,
                timestamp=bar_ts,
                spread_bps=spread_bps,
            )

            exit_signal = OrderSignal(
                agent_id=state.agent_id,
                action="SELL",
                ticker=ticker,
                stop_loss=pos.stop_loss,
                take_profit=pos.take_profit,
                confidence=1.0,
                reasoning=reasoning,
                requested_shares=pos.shares,
                decision_timestamp=dec_ts,
            )

            trade = self.simulate_fill(
                order_or_check=exit_signal,
                fill_quote_or_bar=synthetic_quote,
                fill_timestamp=bar_ts,
                shares=pos.shares,
                state=state,
                exit_reason=exit_reason,
                decision_timestamp=decision_timestamp,  # only enforce strict check when explicitly passed
            )

            if apply_to_state:
                proceeds = round(trade.notional_dollars - trade.commission_dollars, 2)
                state.cash = round(state.cash + proceeds, 2)
                state.positions.pop(ticker, None)
                state.trades.append(trade)
                if bar_ts:
                    state.last_updated = bar_ts

            triggered_trades.append(trade)

        return triggered_trades

    # Aliases for caller convenience
    execute_order = simulate_fill
    fill_order = simulate_fill
