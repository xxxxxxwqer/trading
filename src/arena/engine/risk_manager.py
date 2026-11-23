"""Shared Professional Risk Manager applied uniformly across all 10 agents."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional, Union

from ..config import (
    AGENT_SPECS_BY_ID,
    COMMISSION_DOLLARS,
    DAILY_LOSS_LIMIT_PCT,
    DEFAULT_SPREAD_BPS,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_EQUITY_PCT,
    MAX_RISK_PER_TRADE_PCT,
    MAX_TOTAL_DRAWDOWN_PCT,
    MIN_STOP_DISTANCE_PCT,
    SLIPPAGE_RATE,
    UNIVERSE_SET,
)
from ..models import (
    AgentLedgerState,
    Bar,
    OrderSignal,
    Quote,
    RejectionRecord,
    RiskCheckResult,
)

MIN_ORDER_NOTIONAL_DOLLARS: float = 10.0


class RiskManager:
    """Enforces uniform position-sizing, stop-loss, concentration, and drawdown circuit-breaker rules."""

    def __init__(
        self,
        max_risk_per_trade_pct: float = MAX_RISK_PER_TRADE_PCT,
        max_position_equity_pct: float = MAX_POSITION_EQUITY_PCT,
        max_open_positions: int = MAX_OPEN_POSITIONS,
        daily_loss_limit_pct: float = DAILY_LOSS_LIMIT_PCT,
        max_total_drawdown_pct: float = MAX_TOTAL_DRAWDOWN_PCT,
        min_stop_distance_pct: float = MIN_STOP_DISTANCE_PCT,
    ) -> None:
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_position_equity_pct = max_position_equity_pct
        self.max_open_positions = max_open_positions
        # Store percentage thresholds on 0..100 scale (e.g., -3.0 and -10.0)
        self.daily_loss_limit_pct_100 = (
            daily_loss_limit_pct * 100.0 if abs(daily_loss_limit_pct) <= 1.0 else daily_loss_limit_pct
        )
        self.max_total_drawdown_pct_100 = (
            max_total_drawdown_pct * 100.0 if abs(max_total_drawdown_pct) <= 1.0 else max_total_drawdown_pct
        )
        self.min_stop_distance_pct = min_stop_distance_pct

    @staticmethod
    def _extract_prices(
        quote: Optional[Union[Quote, Bar, float, int]] = None,
        reference_price: Optional[float] = None,
    ) -> tuple[float, float, float]:
        """Return (entry_reference_price, ask_price, bid_price)."""
        half_spread_rate = (DEFAULT_SPREAD_BPS / 2.0) / 10_000.0

        if isinstance(quote, (int, float)):
            ref = float(quote)
            quote = None
            if reference_price is None:
                reference_price = ref

        if isinstance(quote, Quote):
            mid = quote.mid_price if quote.mid_price > 0 else (
                (quote.bid_price + quote.ask_price) / 2.0
                if (quote.bid_price > 0 and quote.ask_price > 0)
                else max(quote.ask_price, quote.bid_price, 0.0)
            )
            ask = quote.ask_price if quote.ask_price >= mid > 0 else mid * (1.0 + half_spread_rate)
            bid = quote.bid_price if 0 < quote.bid_price <= mid else mid * (1.0 - half_spread_rate)
            entry = float(reference_price) if (reference_price is not None and reference_price > 0) else mid
            return entry, ask, bid

        if isinstance(quote, Bar):
            mid = quote.close if quote.close > 0 else quote.open
            ask = mid * (1.0 + half_spread_rate)
            bid = mid * (1.0 - half_spread_rate)
            entry = float(reference_price) if (reference_price is not None and reference_price > 0) else mid
            return entry, ask, bid

        if reference_price is not None and reference_price > 0:
            mid = float(reference_price)
            ask = mid * (1.0 + half_spread_rate)
            bid = mid * (1.0 - half_spread_rate)
            return mid, ask, bid

        return 0.0, 0.0, 0.0

    @staticmethod
    def _record_rejection(
        state: AgentLedgerState,
        signal: OrderSignal,
        reason: str,
        record_rejection: bool = True,
    ) -> RiskCheckResult:
        if record_rejection and signal.action != "HOLD":
            ts = signal.decision_timestamp or state.last_updated or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            state.rejections.append(
                RejectionRecord(
                    timestamp=ts,
                    agent_id=signal.agent_id or state.agent_id,
                    action=signal.action,
                    ticker=signal.ticker,
                    reason=reason,
                    requested_stop_loss=signal.stop_loss,
                    agent_reasoning=signal.reasoning,
                )
            )
        return RiskCheckResult(
            approved=False,
            signal=signal,
            approved_shares=0.0,
            estimated_notional=0.0,
            risk_dollars=0.0,
            rejection_reason=reason,
        )

    def evaluate_order(
        self,
        signal: Union[OrderSignal, AgentLedgerState],
        state: Union[AgentLedgerState, OrderSignal],
        quote: Optional[Union[Quote, Bar, float, int]] = None,
        reference_price: Optional[float] = None,
        bypass_position_cap: Optional[bool] = None,
        record_rejection: bool = True,
    ) -> RiskCheckResult:
        """Evaluate an OrderSignal against the agent's ledger state and current market quote."""
        # Support either (signal, state, ...) or (state, signal, ...) argument ordering
        if isinstance(signal, AgentLedgerState) and isinstance(state, OrderSignal):
            signal, state = state, signal

        assert isinstance(signal, OrderSignal)
        assert isinstance(state, AgentLedgerState)

        action = (signal.action or "HOLD").upper()
        ticker = (signal.ticker or "").strip().upper()

        # 1. HOLD orders require no execution and do not log rejections
        if action == "HOLD":
            return RiskCheckResult(
                approved=False,
                signal=signal,
                approved_shares=0.0,
                estimated_notional=0.0,
                risk_dollars=0.0,
                rejection_reason="HOLD",
            )

        entry_price, ask_price, bid_price = self._extract_prices(quote=quote, reference_price=reference_price)

        # 2. SELL orders: Long or cash only (no shorting). Selling an existing long is always allowed.
        if action == "SELL":
            if not ticker or ticker not in state.positions or state.positions[ticker].shares <= 0:
                return self._record_rejection(
                    state=state,
                    signal=signal,
                    reason=f"Cannot SELL '{ticker}': no open long position (short selling is prohibited)",
                    record_rejection=record_rejection,
                )

            pos = state.positions[ticker]
            shares_to_sell = pos.shares
            if signal.requested_shares is not None and signal.requested_shares > 0:
                capped = math.floor(float(signal.requested_shares) * 10000.0) / 10000.0
                shares_to_sell = min(pos.shares, capped)

            if shares_to_sell <= 0:
                return self._record_rejection(
                    state=state,
                    signal=signal,
                    reason=f"Cannot SELL '{ticker}': requested share quantity <= 0",
                    record_rejection=record_rejection,
                )

            sell_ref = bid_price if bid_price > 0 else (pos.current_price if pos.current_price > 0 else pos.avg_entry_price)
            estimated_fill = round(sell_ref * (1.0 - SLIPPAGE_RATE), 4) if sell_ref > 0 else 0.0
            return RiskCheckResult(
                approved=True,
                signal=signal,
                approved_shares=round(shares_to_sell, 4),
                estimated_notional=round(shares_to_sell * estimated_fill, 2),
                risk_dollars=0.0,
                rejection_reason="",
            )

        if action != "BUY":
            return self._record_rejection(
                state=state,
                signal=signal,
                reason=f"Unsupported order action '{action}'",
                record_rejection=record_rejection,
            )

        # 3. BUY order validations
        # Rule 1: Agent blown-up check (-10% max drawdown)
        if state.status == "BLOWN_UP" or state.current_drawdown_pct <= self.max_total_drawdown_pct_100 + 1e-9:
            return self._record_rejection(
                state=state,
                signal=signal,
                reason="Agent is BLOWN_UP (-10% max drawdown breached)",
                record_rejection=record_rejection,
            )

        # Rule 2: Daily loss limit check (-3.0% daily PnL)
        if state.status == "DAILY_HALTED" or state.daily_pnl_pct <= self.daily_loss_limit_pct_100 + 1e-9:
            return self._record_rejection(
                state=state,
                signal=signal,
                reason="Daily loss limit (-3.0%) reached; no new trades allowed today",
                record_rejection=record_rejection,
            )

        if state.status == "COMPLETED":
            return self._record_rejection(
                state=state,
                signal=signal,
                reason="Competition is COMPLETED; no new BUY orders allowed",
                record_rejection=record_rejection,
            )

        # Rule 3: Universe membership check
        if not ticker or ticker not in UNIVERSE_SET:
            return self._record_rejection(
                state=state,
                signal=signal,
                reason=f"Ticker '{ticker}' is not in the approved liquid US stock/ETF universe",
                record_rejection=record_rejection,
            )

        # Rule 4: Max open positions (5)
        open_positions_count = sum(1 for p in state.positions.values() if p.shares > 0)
        has_existing_position = ticker in state.positions and state.positions[ticker].shares > 0
        if not has_existing_position and open_positions_count >= self.max_open_positions:
            return self._record_rejection(
                state=state,
                signal=signal,
                reason=f"Max open positions ({self.max_open_positions}) reached",
                record_rejection=record_rejection,
            )

        if entry_price <= 0 or ask_price <= 0:
            return self._record_rejection(
                state=state,
                signal=signal,
                reason=f"Invalid or unavailable reference price for '{ticker}'",
                record_rejection=record_rejection,
            )

        # Rule 5: Stop-loss requirement and minimum stop distance
        if signal.stop_loss is None:
            return self._record_rejection(
                state=state,
                signal=signal,
                reason="Missing stop-loss: every BUY order must include a valid stop_loss",
                record_rejection=record_rejection,
            )

        stop_loss = float(signal.stop_loss)
        if stop_loss <= 0.0 or stop_loss >= entry_price:
            return self._record_rejection(
                state=state,
                signal=signal,
                reason=(
                    f"Invalid stop-loss ({stop_loss:.4f}): must be > 0 and strictly below "
                    f"entry price ({entry_price:.4f})"
                ),
                record_rejection=record_rejection,
            )

        stop_distance = entry_price - stop_loss
        stop_distance_pct = stop_distance / entry_price
        if stop_distance_pct < self.min_stop_distance_pct - 1e-9:
            return self._record_rejection(
                state=state,
                signal=signal,
                reason=(
                    f"Stop-loss distance ({stop_distance_pct * 100.0:.3f}%) is tighter than "
                    f"minimum allowed ({self.min_stop_distance_pct * 100.0:.2f}%)"
                ),
                record_rejection=record_rejection,
            )

        # Rule 6: Risk per trade (1% of equity) & max position cap (20% of equity, or 98% cash for ctrl_spy)
        max_risk_dollars = max(0.0, state.equity * self.max_risk_per_trade_pct)
        risk_sized_shares = max_risk_dollars / stop_distance

        if bypass_position_cap is None:
            spec = AGENT_SPECS_BY_ID.get(state.agent_id) or AGENT_SPECS_BY_ID.get(signal.agent_id)
            use_bypass_cap = bool(spec and spec.bypass_position_cap)
        else:
            use_bypass_cap = bool(bypass_position_cap)

        existing_pos = state.positions.get(ticker)
        existing_mv = (
            existing_pos.market_value
            if (existing_pos is not None and existing_pos.market_value > 0)
            else (existing_pos.shares * entry_price if existing_pos is not None else 0.0)
        )

        if use_bypass_cap:
            max_position_dollars = max(0.0, state.cash * 0.98 - existing_mv)
            position_cap_shares = max_position_dollars / entry_price
            raw_shares = position_cap_shares
        else:
            max_position_dollars = max(0.0, state.equity * self.max_position_equity_pct - existing_mv)
            position_cap_shares = max_position_dollars / entry_price
            raw_shares = min(risk_sized_shares, position_cap_shares)

        if signal.requested_shares is not None and signal.requested_shares > 0:
            raw_shares = min(raw_shares, float(signal.requested_shares))

        # Cap by available cash accounting for ask half-spread and 5 bps slippage
        effective_buy_fill_price = round(ask_price * (1.0 + SLIPPAGE_RATE), 4)
        available_cash = max(0.0, state.cash - COMMISSION_DOLLARS)
        cash_cap_shares = available_cash / effective_buy_fill_price if effective_buy_fill_price > 0 else 0.0
        raw_shares = min(raw_shares, cash_cap_shares)

        shares = math.floor(max(0.0, raw_shares) * 10000.0) / 10000.0
        while shares > 0 and round(shares * effective_buy_fill_price, 2) > available_cash:
            shares = round(max(0.0, shares - 0.0001), 4)

        estimated_notional = round(shares * effective_buy_fill_price, 2)
        actual_risk_dollars = round(shares * stop_distance, 2)

        if shares <= 0.0 or estimated_notional < MIN_ORDER_NOTIONAL_DOLLARS:
            return self._record_rejection(
                state=state,
                signal=signal,
                reason=(
                    f"Order notional (${estimated_notional:.2f}, shares={shares:.4f}) is below "
                    f"${MIN_ORDER_NOTIONAL_DOLLARS:.2f} minimum after position/cash caps"
                ),
                record_rejection=record_rejection,
            )

        return RiskCheckResult(
            approved=True,
            signal=signal,
            approved_shares=shares,
            estimated_notional=estimated_notional,
            risk_dollars=actual_risk_dollars,
            rejection_reason="",
        )

    # Convenient aliases for caller flexibility
    evaluate = evaluate_order
    check_order = evaluate_order
    validate_order = evaluate_order
