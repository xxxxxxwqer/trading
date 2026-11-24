"""Unit tests for Agent 14 — Claude Residual-Momentum Pullback (`claude_rmp`)."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from arena.agents.claude_rmp_agent import (
    ClaudeRMPAgent,
    compute_daily_regime_and_leaders,
    compute_residual_momentum_score,
    compute_wilder_atr,
    compute_wilder_rsi,
    load_claude_rmp_params,
)
from arena.models import (
    AgentLedgerState,
    Bar,
    MarketSnapshot,
    NewsHeadline,
    Position,
    Quote,
    TickerIndicators,
)


def _make_daily_bars(ticker: str, n: int = 80, start_px: float = 100.0, daily_drift: float = 0.002, vol: float = 0.0) -> list[Bar]:
    bars: list[Bar] = []
    px = start_px
    for i in range(n):
        wave = math.sin(i * 0.7) * vol
        px = max(10.0, px * math.exp(daily_drift + wave))
        bars.append(
            Bar(
                ticker=ticker,
                timestamp=f"2026-{(i // 28) + 7:02d}-{(i % 28) + 1:02d}T21:00:00Z",
                open=round(px * 0.998, 4),
                high=round(px * 1.012, 4),
                low=round(px * 0.988, 4),
                close=round(px, 4),
                volume=2_000_000.0,
            )
        )
    return bars


class TestClaudeRMP(unittest.TestCase):
    def test_wilder_atr_and_wilder_rsi2_and_intercept_free_residual_score(self) -> None:
        spy_bars = _make_daily_bars("SPY", n=80, start_px=500.0, daily_drift=0.001, vol=0.004)
        # Stock outperforming SPY with low residual noise -> high positive residual momentum score
        leader_bars = _make_daily_bars("NVDA", n=80, start_px=100.0, daily_drift=0.0045, vol=0.005)
        laggard_bars = _make_daily_bars("INTC", n=80, start_px=100.0, daily_drift=-0.002, vol=0.005)

        atr = compute_wilder_atr(leader_bars, 14)
        self.assertIsNotNone(atr)
        self.assertGreater(atr, 0.0)

        # Sharp 2-day pullback triggers RSI(2) <= 30
        closes = [b.close for b in leader_bars[-20:]]
        pulled_back = closes + [closes[-1] * 0.96, closes[-1] * 0.93]
        rsi2 = compute_wilder_rsi(pulled_back, period=2)
        self.assertIsNotNone(rsi2)
        self.assertLessEqual(rsi2, 30.0)

        sc_leader = compute_residual_momentum_score(
            [b.close for b in leader_bars],
            [b.close for b in spy_bars],
        )
        sc_laggard = compute_residual_momentum_score(
            [b.close for b in laggard_bars],
            [b.close for b in spy_bars],
        )
        self.assertIsNotNone(sc_leader)
        self.assertIsNotNone(sc_laggard)
        self.assertGreater(sc_leader[0], 0.0)
        self.assertLess(sc_laggard[0], 0.0)

    def test_regime_gate_and_pullback_entry_e1_to_e6_and_news_veto(self) -> None:
        params = load_claude_rmp_params()
        spy_bars = _make_daily_bars("SPY", n=80, start_px=500.0, daily_drift=0.001, vol=0.003)
        nvda_bars = _make_daily_bars("NVDA", n=80, start_px=100.0, daily_drift=0.004, vol=0.004)
        # Create a 2-day pullback at end of nvda_bars while staying above SMA50
        h10 = max(b.close for b in nvda_bars[-10:])
        atr14 = compute_wilder_atr(nvda_bars, 14)
        assert atr14 is not None
        p_pullback = round(h10 - 1.8 * atr14, 2)  # E1: 1.8 ATR pullback in [1.0, 3.5]

        daily_map = {"SPY": spy_bars, "NVDA": nvda_bars}
        # Populate enough stocks above SMA50 for breadth >= 50% (C3)
        for sym in ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "JPM"):
            daily_map[sym] = _make_daily_bars(sym, n=80, start_px=150.0, daily_drift=0.002, vol=0.003)

        reg = compute_daily_regime_and_leaders(daily_map, params, eligible_stocks=list(daily_map.keys())[1:])
        self.assertTrue(reg["valid"])
        self.assertEqual(reg["regime_score"], 4)
        self.assertEqual(reg["slots"], 5)
        self.assertEqual(reg["leaders_top10"][0]["ticker"], "NVDA")

        spy_prev = spy_bars[-1].close
        nvda_prev = nvda_bars[-1].close
        # Make intraday pullback price bounce off day low (E4) and stay within -1.5% relative to SPY (E5)
        # Adjust last daily bar slightly so intraday move from prev_close is mild (-0.5%) while 1.8 ATR below H10
        nvda_bars[-1].close = round(p_pullback * 1.005, 4)
        nvda_bars[-2].close = round(p_pullback * 1.02, 4)

        intraday_bars = {
            "SPY": [
                Bar("SPY", "2026-11-23T14:30:00Z", spy_prev, spy_prev * 1.002, spy_prev * 0.999, spy_prev, 1_000_000),
                Bar("SPY", "2026-11-23T15:00:00Z", spy_prev, spy_prev * 1.002, spy_prev * 0.999, spy_prev, 1_000_000),
            ],
            "NVDA": [
                Bar("NVDA", "2026-11-23T14:30:00Z", p_pullback, p_pullback * 1.002, p_pullback - 0.5 * atr14, p_pullback - 0.25 * atr14, 500_000),
                Bar("NVDA", "2026-11-23T15:00:00Z", p_pullback - 0.2 * atr14, p_pullback + 0.1, p_pullback - 0.3 * atr14, p_pullback, 500_000),
            ],
        }
        indicators = {
            "SPY": TickerIndicators("SPY", spy_prev, spy_prev, 0.0, 1.0, spy_prev, spy_prev, spy_prev, 55.0, 3.0, spy_prev * 1.02, spy_prev, spy_prev * 0.98, spy_prev * 1.01, spy_prev * 0.97),
            "NVDA": TickerIndicators("NVDA", p_pullback, nvda_bars[-1].close, -0.5, 1.0, p_pullback, p_pullback, p_pullback * 0.95, 40.0, atr14, p_pullback * 1.04, p_pullback, p_pullback * 0.96, h10, p_pullback * 0.95),
        }
        quotes = {
            "SPY": Quote("SPY", "2026-11-23T15:00:00Z", spy_prev - 0.05, spy_prev + 0.05, spy_prev, 1.6),
            "NVDA": Quote("NVDA", "2026-11-23T15:00:00Z", p_pullback - 0.02, p_pullback + 0.02, p_pullback, 3.0),
        }

        with tempfile.TemporaryDirectory() as tmp:
            agent = ClaudeRMPAgent(state_dir=Path(tmp))
            state = AgentLedgerState(agent_id="claude_rmp", name=agent.name, category=agent.category)
            snap = MarketSnapshot(
                decision_timestamp="2026-11-23T15:00:00Z",  # 10:00 ET (inside 10:00-15:30 entry window)
                trading_date_et="2026-11-23",
                trading_day_number=1,
                is_near_market_close=False,
                session_open_et="09:30",
                session_close_et="16:00",
                bars_by_ticker=intraday_bars,
                quotes_by_ticker=quotes,
                indicators=indicators,
                news=[],
                daily_bars_by_ticker=daily_map,
            )
            sigs = agent.decide_batch(snap, state)
            self.assertEqual(sigs[0].action, "BUY")
            self.assertEqual(sigs[0].ticker, "NVDA")
            self.assertIn("ENTRY_PULLBACK", sigs[0].reasoning)

        # Now add a negative keyword headline ("downgrade") -> E6 news veto blocks NVDA!
        with tempfile.TemporaryDirectory() as tmp2:
            agent2 = ClaudeRMPAgent(state_dir=Path(tmp2))
            state2 = AgentLedgerState(agent_id="claude_rmp", name=agent2.name, category=agent2.category)
            snap_veto = MarketSnapshot(
                decision_timestamp="2026-11-23T15:00:00Z",
                trading_date_et="2026-11-23",
                trading_day_number=1,
                is_near_market_close=False,
                session_open_et="09:30",
                session_close_et="16:00",
                bars_by_ticker=intraday_bars,
                quotes_by_ticker=quotes,
                indicators=indicators,
                news=[NewsHeadline("Analyst issues downgrade on NVDA", "", "Alpaca", "2026-11-23T13:00:00Z", ["NVDA"])],
                daily_bars_by_ticker=daily_map,
            )
            sigs2 = agent2.decide_batch(snap_veto, state2)
            self.assertEqual(sigs2[0].action, "HOLD")

    def test_day10_blocks_new_entries_and_stop_ratchets_up_only(self) -> None:
        spy_bars = _make_daily_bars("SPY", n=80, start_px=500.0, daily_drift=0.001, vol=0.003)
        nvda_bars = _make_daily_bars("NVDA", n=80, start_px=100.0, daily_drift=0.004, vol=0.004)
        daily_map = {"SPY": spy_bars, "NVDA": nvda_bars}
        for sym in ("AAPL", "MSFT", "GOOGL", "AMZN"):
            daily_map[sym] = _make_daily_bars(sym, n=80, start_px=150.0, daily_drift=0.002, vol=0.003)

        with tempfile.TemporaryDirectory() as tmp:
            agent = ClaudeRMPAgent(state_dir=Path(tmp))
            state = AgentLedgerState(agent_id="claude_rmp", name=agent.name, category=agent.category)
            # Open position at $100 with initial stop $95 (R=$5); current price $106 (+1.2R -> stop ratchets to >= $100.50)
            state.positions["NVDA"] = Position(
                ticker="NVDA", shares=15.0, avg_entry_price=100.0, current_price=106.0,
                stop_loss=95.0, take_profit=None, opened_at="2026-11-18T15:00:00Z",
            )
            spy_prev = spy_bars[-1].close
            from arena.config import COMPETITION_TRADING_DAYS

            snap_d10 = MarketSnapshot(
                decision_timestamp="2026-11-23T15:00:00Z",
                trading_date_et="2026-11-23",
                trading_day_number=COMPETITION_TRADING_DAYS,  # Final trading day -> NO new entries!
                is_near_market_close=False,
                session_open_et="09:30",
                session_close_et="16:00",
                bars_by_ticker={"SPY": [Bar("SPY", "2026-11-23T15:00:00Z", spy_prev, spy_prev, spy_prev, spy_prev, 1_000_000)], "NVDA": [Bar("NVDA", "2026-11-23T15:00:00Z", 105.0, 106.5, 104.5, 106.0, 500_000)]},
                quotes_by_ticker={"SPY": Quote("SPY", "2026-11-23T15:00:00Z", spy_prev, spy_prev, spy_prev, 2.0), "NVDA": Quote("NVDA", "2026-11-23T15:00:00Z", 105.98, 106.02, 106.0, 3.8)},
                indicators={
                    "SPY": TickerIndicators("SPY", spy_prev, spy_prev, 0.0, 1.0, spy_prev, spy_prev, spy_prev, 55.0, 3.0, spy_prev * 1.02, spy_prev, spy_prev * 0.98, spy_prev * 1.01, spy_prev * 0.97),
                    "NVDA": TickerIndicators("NVDA", 106.0, 104.0, 1.9, 5.0, 103.0, 101.0, 99.0, 65.0, 2.0, 108.0, 101.0, 94.0, 106.5, 98.0),
                },
                daily_bars_by_ticker=daily_map,
            )
            sigs = agent.decide_batch(snap_d10, state)
            self.assertEqual(sigs[0].action, "HOLD")
            self.assertIn("SKIP_DAY10", sigs[0].reasoning)
            # Stop must have ratcheted up to at least entry + 0.1R = $100.50!
            self.assertGreaterEqual(state.positions["NVDA"].stop_loss, 100.50)


if __name__ == "__main__":
    unittest.main()
