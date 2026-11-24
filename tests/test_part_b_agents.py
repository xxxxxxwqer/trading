"""Unit tests for all 20 Part B quantitative agents (#16–#35) and 34-agent roster integrity."""

from __future__ import annotations

import math
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from arena.agents import CODED_AND_CONTROL_AGENT_CLASSES, build_all_agents  # noqa: E402
from arena.agents.allocation_agents import _solve_long_only_min_variance  # noqa: E402
from arena.agents.base import compute_strategy_hashes  # noqa: E402
from arena.agents.calendar_agents import OvernightDriftAgent, _is_turn_of_month_day  # noqa: E402
from arena.agents.part_b_common import (  # noqa: E402
    compute_session_vwap_and_std,
    strict_prior_daily_bars,
)
from arena.config import AGENT_SPECS, UNIVERSE  # noqa: E402
from arena.engine.risk_manager import RiskManager  # noqa: E402
from arena.models import (  # noqa: E402
    AgentLedgerState,
    Bar,
    MarketSnapshot,
    Position,
    Quote,
    TickerIndicators,
)


def _make_full_universe_snapshot(
    trading_date_et: str = "2026-11-30",  # Last trading day of Nov 2026 (Turn-of-Month!)
    decision_ts: str = "2026-11-30T15:30:00Z",  # 10:30 EST
    session_close_et: str = "16:00",
    spy_3d_crash: bool = False,
) -> MarketSnapshot:
    """Build a complete deterministic 71-ticker MarketSnapshot with 85 prior daily bars & intraday 30m bars."""
    end_d = date.fromisoformat(trading_date_et) - timedelta(days=1)
    dates: List[date] = []
    cur = end_d
    while len(dates) < 85:
        if cur.weekday() < 5:
            dates.append(cur)
        cur -= timedelta(days=1)
    dates.reverse()

    daily_bars: Dict[str, List[Bar]] = {}
    bars_30m: Dict[str, List[Bar]] = {}
    quotes: Dict[str, Quote] = {}
    indicators: Dict[str, TickerIndicators] = {}

    for idx, sym in enumerate(UNIVERSE):
        px = 80.0 + (idx % 20) * 15.0
        arr: List[Bar] = []
        drift = 0.0012 if (idx % 3 != 0) else -0.0004
        vol_scale = 0.006 + (idx % 5) * 0.003
        for di, d in enumerate(dates):
            step = drift + vol_scale * math.sin((di + idx) * 0.45)
            if sym == "SPY" and spy_3d_crash and di >= len(dates) - 3:
                step = -0.015
            px = max(10.0, px * math.exp(step))
            arr.append(
                Bar(
                    ticker=sym,
                    timestamp=f"{d.isoformat()}T21:00:00Z",
                    open=round(px * 0.996, 4),
                    high=round(px * 1.008, 4),
                    low=round(px * 0.992, 4),
                    close=round(px, 4),
                    volume=2_000_000.0 + (idx * 50_000.0),
                )
            )
        daily_bars[sym] = arr
        prev_c = arr[-1].close
        last_px = round(prev_c * (1.003 if idx % 2 == 0 else 0.996), 4)
        bars_30m[sym] = [
            Bar(sym, f"{trading_date_et}T14:30:00Z", prev_c * 0.999, prev_c * 1.004, prev_c * 0.995, prev_c * 1.002, 400_000),
            Bar(sym, f"{trading_date_et}T15:00:00Z", prev_c * 1.002, last_px * 1.003, last_px * 0.997, last_px, 450_000),
        ]
        quotes[sym] = Quote(
            ticker=sym,
            timestamp=decision_ts,
            bid_price=round(last_px * 0.9998, 4),
            ask_price=round(last_px * 1.0002, 4),
            mid_price=last_px,
            spread_bps=4.0,
        )
        indicators[sym] = TickerIndicators(
            ticker=sym,
            last_price=last_px,
            prev_close=prev_c,
            change_pct_1d=round(((last_px / prev_c) - 1.0) * 100.0, 2),
            change_pct_5d=1.2,
            sma_10=round(last_px * 0.995, 4),
            sma_20=round(last_px * 0.99, 4),
            sma_30=round(last_px * 0.985, 4),
            rsi_14=55.0,
            atr_14=round(last_px * 0.015, 4),
            bb_upper_20=round(last_px * 1.02, 4),
            bb_mid_20=round(last_px * 0.99, 4),
            bb_lower_20=round(last_px * 0.96, 4),
            high_20=round(last_px * 1.025, 4),
            low_20=round(last_px * 0.96, 4),
            volume_ratio=1.35,
            relative_strength_vs_spy=0.4,
        )

    return MarketSnapshot(
        decision_timestamp=decision_ts,
        trading_date_et=trading_date_et,
        trading_day_number=1,
        is_near_market_close=False,
        session_open_et="09:30",
        session_close_et=session_close_et,
        bars_by_ticker=bars_30m,
        quotes_by_ticker=quotes,
        indicators=indicators,
        news=[],
        daily_bars_by_ticker=daily_bars,
    )


class TestPartBAgents(unittest.TestCase):
    def test_all_34_agents_instantiate_and_part_b_hashes_locked(self) -> None:
        agents = build_all_agents(allow_offline_llm_simulation=True)
        self.assertEqual(len(AGENT_SPECS), 34)
        self.assertEqual(len(agents), 34)

        hashes = compute_strategy_hashes()
        for fname in (
            "part_b_params.json",
            "part_b_common.py",
            "calendar_agents.py",
            "allocation_agents.py",
            "factor_agents.py",
            "timing_agents.py",
            "microstructure_agents.py",
        ):
            self.assertIn(fname, hashes)
            self.assertEqual(len(hashes[fname]), 64)

    def test_no_lookahead_strict_prior_daily_bars_ignores_today(self) -> None:
        snap = _make_full_universe_snapshot("2026-11-30")
        clean_len = len(strict_prior_daily_bars(snap, "SPY"))
        # Inject a same-day look-ahead bar
        snap.daily_bars_by_ticker["SPY"].append(
            Bar("SPY", "2026-11-30T21:00:00Z", 999.0, 999.0, 999.0, 999.0, 1_000_000)
        )
        after_bars = strict_prior_daily_bars(snap, "SPY")
        self.assertEqual(len(after_bars), clean_len)
        self.assertNotEqual(after_bars[-1].close, 999.0)

    def test_all_20_part_b_agents_deterministic_and_pass_shared_risk_manager(self) -> None:
        snap = _make_full_universe_snapshot("2026-11-30", decision_ts="2026-11-30T15:30:00Z")
        part_b_specs = list(AGENT_SPECS[14:])
        self.assertEqual(len(part_b_specs), 20)

        rm = RiskManager()
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            agents1 = {
                sp.agent_id: CODED_AND_CONTROL_AGENT_CLASSES[sp.agent_id](spec=sp, state_dir=Path(tmp1))
                for sp in part_b_specs
            }
            agents2 = {
                sp.agent_id: CODED_AND_CONTROL_AGENT_CLASSES[sp.agent_id](spec=sp, state_dir=Path(tmp2))
                for sp in part_b_specs
            }

            for sp in part_b_specs:
                aid = sp.agent_id
                st1 = AgentLedgerState(agent_id=aid, name=agents1[aid].name, category=agents1[aid].category)
                st2 = AgentLedgerState(agent_id=aid, name=agents2[aid].name, category=agents2[aid].category)
                s1 = agents1[aid].decide_batch(snap, st1)
                s2 = agents2[aid].decide_batch(snap, st2)
                self.assertEqual([x.to_dict() for x in s1], [x.to_dict() for x in s2], f"Non-deterministic: {aid}")

                # Verify every emitted BUY passes the shared RiskManager cleanly
                for sig in s1:
                    if sig.action == "BUY":
                        res = rm.evaluate(sig, st1, quote=snap.quotes_by_ticker.get(sig.ticker))
                        self.assertTrue(res.approved, f"{aid} BUY {sig.ticker} rejected by RM: {res.rejection_reason}")
                        self.assertLessEqual(res.estimated_notional, st1.equity * 0.20 + 1.0)

    def test_calendar_and_math_helpers(self) -> None:
        # Nov 30, 2026 is last trading day of Nov; Dec 1, 2, 3 are first 3 trading days of Dec; Dec 15 is mid-month
        self.assertTrue(_is_turn_of_month_day("2026-11-30", last_n=1, first_n=3))
        self.assertTrue(_is_turn_of_month_day("2026-12-01", last_n=1, first_n=3))
        self.assertTrue(_is_turn_of_month_day("2026-12-03", last_n=1, first_n=3))
        self.assertFalse(_is_turn_of_month_day("2026-12-15", last_n=1, first_n=3))

        # Min-variance solver puts most weight on the lowest-variance asset
        cov = [[0.0001, 0.0], [0.0, 0.01]]
        w = _solve_long_only_min_variance(cov, iterations=80, ridge=1e-6)
        self.assertAlmostEqual(sum(w), 1.0, places=4)
        self.assertGreater(w[0], 0.90)

        # VWAP and std computation
        bars = [
            Bar("AAPL", "2026-11-30T14:30:00Z", 100, 102, 98, 100, 1000),
            Bar("AAPL", "2026-11-30T15:00:00Z", 100, 106, 100, 104, 1000),
        ]
        vwap, sd = compute_session_vwap_and_std(bars)
        self.assertIsNotNone(vwap)
        self.assertIsNotNone(sd)
        self.assertGreater(sd, 0.0)

    def test_overnight_drift_close_entry_and_morning_unwind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = OvernightDriftAgent(state_dir=Path(tmp))
            st = AgentLedgerState(agent_id="overnight_drift", name=agent.name, category=agent.category)

            # 15:30 ET (20:30 UTC) -> enters SPY & QQQ
            snap_close = _make_full_universe_snapshot("2026-11-30", decision_ts="2026-11-30T20:30:00Z")
            sigs_in = agent.decide_batch(snap_close, st)
            buy_tickers = {s.ticker for s in sigs_in if s.action == "BUY"}
            self.assertEqual(buy_tickers, {"SPY", "QQQ"})

            # Next morning 09:30 ET (14:30 UTC) -> unwinds open positions
            st.positions["SPY"] = Position("SPY", 3.0, 500.0, 502.0, 485.0, None, "2026-11-30T20:31:00Z")
            snap_open = _make_full_universe_snapshot("2026-12-01", decision_ts="2026-12-01T14:30:00Z")
            sigs_out = agent.decide_batch(snap_open, st)
            self.assertEqual(len(sigs_out), 1)
            self.assertEqual(sigs_out[0].action, "SELL")
            self.assertEqual(sigs_out[0].ticker, "SPY")


if __name__ == "__main__":
    unittest.main()
