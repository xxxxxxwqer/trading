"""Unit tests for the shared RiskManager."""

from __future__ import annotations

import unittest

from src.arena.engine.risk_manager import RiskManager
from src.arena.models import AgentLedgerState, OrderSignal, Position, Quote


def _make_state(
    agent_id: str = "coded_trend",
    cash: float = 10_000.0,
    equity: float = 10_000.0,
    status: str = "ACTIVE",
    daily_pnl_pct: float = 0.0,
    current_drawdown_pct: float = 0.0,
) -> AgentLedgerState:
    return AgentLedgerState(
        agent_id=agent_id,
        name="Test Agent",
        category="Coded Strategy",
        status=status,  # type: ignore[arg-type]
        cash=cash,
        equity=equity,
        peak_equity=max(10_000.0, equity),
        day_start_equity=10_000.0,
        daily_pnl_pct=daily_pnl_pct,
        current_drawdown_pct=current_drawdown_pct,
    )


def _make_quote(ticker: str = "AAPL", mid: float = 100.0, spread_bps: float = 4.0) -> Quote:
    half = mid * (spread_bps / 20_000.0)
    return Quote(
        ticker=ticker,
        timestamp="2026-11-12T15:00:00Z",
        bid_price=round(mid - half, 4),
        ask_price=round(mid + half, 4),
        mid_price=mid,
        spread_bps=spread_bps,
    )


class TestRiskManager(unittest.TestCase):
    def setUp(self) -> None:
        self.rm = RiskManager()

    def test_hold_signal_not_approved_and_not_logged_as_rejection(self) -> None:
        state = _make_state()
        sig = OrderSignal(agent_id=state.agent_id, action="HOLD", ticker="", reasoning="No setup")
        res = self.rm.evaluate_order(sig, state)
        self.assertFalse(res.approved)
        self.assertEqual(res.rejection_reason, "HOLD")
        self.assertEqual(len(state.rejections), 0)

    def test_one_percent_risk_sizing_when_stop_is_wide(self) -> None:
        # Equity = $10,000 -> 1% max risk = $100.
        # Entry = $100, Stop = $90 -> stop_distance = $10 -> risk_sized_shares = 10 shares ($1,000 notional < $2,000 cap).
        state = _make_state()
        quote = _make_quote("AAPL", mid=100.0)
        sig = OrderSignal(
            agent_id=state.agent_id,
            action="BUY",
            ticker="AAPL",
            stop_loss=90.0,
            take_profit=120.0,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        res = self.rm.evaluate_order(sig, state, quote)
        self.assertTrue(res.approved)
        self.assertAlmostEqual(res.approved_shares, 10.0, places=4)
        self.assertAlmostEqual(res.risk_dollars, 100.0, places=2)

    def test_twenty_percent_position_cap_when_stop_is_tight(self) -> None:
        # Equity = $10,000 -> 1% risk = $100.
        # Entry = $100, Stop = $99 -> stop_distance = $1 -> risk_sized_shares = 100 shares ($10,000).
        # 20% position cap = $2,000 -> capped to 20 shares.
        state = _make_state()
        quote = _make_quote("NVDA", mid=100.0)
        sig = OrderSignal(
            agent_id=state.agent_id,
            action="BUY",
            ticker="NVDA",
            stop_loss=99.0,
            take_profit=105.0,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        res = self.rm.evaluate_order(sig, state, quote)
        self.assertTrue(res.approved)
        self.assertAlmostEqual(res.approved_shares, 20.0, places=4)
        self.assertLessEqual(res. approved_shares * 100.0, 2_000.0)

    def test_existing_position_reduces_remaining_twenty_percent_cap(self) -> None:
        state = _make_state()
        pos = Position(
            ticker="MSFT",
            shares=15.0,
            avg_entry_price=100.0,
            current_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            opened_at="2026-11-12T14:30:00Z",
            market_value=1_500.0,
        )
        state.positions["MSFT"] = pos
        quote = _make_quote("MSFT", mid=100.0)
        sig = OrderSignal(
            agent_id=state.agent_id,
            action="BUY",
            ticker="MSFT",
            stop_loss=99.0,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        res = self.rm.evaluate_order(sig, state, quote)
        self.assertTrue(res.approved)
        # Remaining cap is $2,000 - $1,500 = $500 -> 5 shares
        self.assertAlmostEqual(res.approved_shares, 5.0, places=4)

    def test_bypass_position_cap_for_ctrl_spy(self) -> None:
        state = _make_state(agent_id="ctrl_spy")
        quote = _make_quote("SPY", mid=500.0)
        sig = OrderSignal(
            agent_id="ctrl_spy",
            action="BUY",
            ticker="SPY",
            stop_loss=450.0,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        res = self.rm.evaluate_order(sig, state, quote)
        self.assertTrue(res.approved)
        # 98% of $10,000 = $9,800 / $500 = 19.6 shares (> 20% cap of $2,000)
        self.assertAlmostEqual(res.approved_shares, 19.6, places=4)
        self.assertGreater(res.estimated_notional, 9_500.0)

    def test_max_five_open_positions_enforced(self) -> None:
        state = _make_state()
        for ticker in ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]:
            state.positions[ticker] = Position(
                ticker=ticker,
                shares=5.0,
                avg_entry_price=100.0,
                current_price=100.0,
                stop_loss=95.0,
                take_profit=110.0,
                opened_at="2026-11-12T14:30:00Z",
                market_value=500.0,
            )

        sig = OrderSignal(
            agent_id=state.agent_id,
            action="BUY",
            ticker="META",
            stop_loss=95.0,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        res = self.rm.evaluate_order(sig, state, _make_quote("META", mid=100.0))
        self.assertFalse(res.approved)
        self.assertIn("Max open positions (5) reached", res.rejection_reason)
        self.assertEqual(len(state.rejections), 1)

    def test_missing_or_invalid_stop_loss_rejected(self) -> None:
        state = _make_state()
        quote = _make_quote("AAPL", mid=100.0)

        # Missing stop_loss
        sig_no_stop = OrderSignal(
            agent_id=state.agent_id,
            action="BUY",
            ticker="AAPL",
            stop_loss=None,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        res1 = self.rm.evaluate_order(sig_no_stop, state, quote)
        self.assertFalse(res1.approved)
        self.assertIn("stop-loss", res1.rejection_reason.lower())

        # Stop-loss above entry price
        sig_high_stop = OrderSignal(
            agent_id=state.agent_id,
            action="BUY",
            ticker="AAPL",
            stop_loss=101.0,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        res2 = self.rm.evaluate_order(sig_high_stop, state, quote)
        self.assertFalse(res2.approved)

        # Stop-loss too tight (< 0.2%)
        sig_tight_stop = OrderSignal(
            agent_id=state.agent_id,
            action="BUY",
            ticker="AAPL",
            stop_loss=99.95,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        res3 = self.rm.evaluate_order(sig_tight_stop, state, quote)
        self.assertFalse(res3.approved)
        self.assertEqual(len(state.rejections), 3)

    def test_daily_loss_limit_halts_buys_but_allows_sells(self) -> None:
        state = _make_state(status="DAILY_HALTED", daily_pnl_pct=-3.15)
        state.positions["AAPL"] = Position(
            ticker="AAPL",
            shares=10.0,
            avg_entry_price=100.0,
            current_price=96.0,
            stop_loss=92.0,
            take_profit=110.0,
            opened_at="2026-11-12T14:30:00Z",
            market_value=960.0,
        )

        buy_sig = OrderSignal(
            agent_id=state.agent_id,
            action="BUY",
            ticker="MSFT",
            stop_loss=95.0,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        buy_res = self.rm.evaluate_order(buy_sig, state, _make_quote("MSFT", mid=100.0))
        self.assertFalse(buy_res.approved)
        self.assertIn("Daily loss limit (-3.0%) reached", buy_res.rejection_reason)

        sell_sig = OrderSignal(
            agent_id=state.agent_id,
            action="SELL",
            ticker="AAPL",
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        sell_res = self.rm.evaluate_order(sell_sig, state, _make_quote("AAPL", mid=96.0))
        self.assertTrue(sell_res.approved)
        self.assertAlmostEqual(sell_res.approved_shares, 10.0, places=4)

    def test_blown_up_blocks_buys_but_allows_sells(self) -> None:
        state = _make_state(status="BLOWN_UP", current_drawdown_pct=-10.2)
        state.positions["AAPL"] = Position(
            ticker="AAPL",
            shares=8.0,
            avg_entry_price=100.0,
            current_price=90.0,
            stop_loss=85.0,
            take_profit=110.0,
            opened_at="2026-11-12T14:30:00Z",
            market_value=720.0,
        )

        buy_sig = OrderSignal(
            agent_id=state.agent_id,
            action="BUY",
            ticker="NVDA",
            stop_loss=95.0,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        buy_res = self.rm.evaluate_order(buy_sig, state, _make_quote("NVDA", mid=100.0))
        self.assertFalse(buy_res.approved)
        self.assertIn("BLOWN_UP", buy_res.rejection_reason)

        sell_sig = OrderSignal(
            agent_id=state.agent_id,
            action="SELL",
            ticker="AAPL",
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        sell_res = self.rm.evaluate_order(sell_sig, state, _make_quote("AAPL", mid=90.0))
        self.assertTrue(sell_res.approved)

    def test_no_short_selling_allowed(self) -> None:
        state = _make_state()
        sell_sig = OrderSignal(
            agent_id=state.agent_id,
            action="SELL",
            ticker="AAPL",
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        res = self.rm.evaluate_order(sell_sig, state, _make_quote("AAPL", mid=100.0))
        self.assertFalse(res.approved)
        self.assertIn("short selling is prohibited", res.rejection_reason)
        self.assertEqual(len(state.rejections), 1)

    def test_cash_cap_prevents_negative_cash_after_spread_and_slippage(self) -> None:
        state = _make_state(cash=500.0, equity=10_000.0)
        quote = _make_quote("AAPL", mid=100.0, spread_bps=4.0)
        sig = OrderSignal(
            agent_id=state.agent_id,
            action="BUY",
            ticker="AAPL",
            stop_loss=98.0,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        res = self.rm.evaluate_order(sig, state, quote)
        self.assertTrue(res.approved)
        self.assertLessEqual(res.estimated_notional, 500.0)


if __name__ == "__main__":
    unittest.main()
