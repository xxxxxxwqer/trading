"""Unit tests for Agent #15 — Kiro Pair-Spread Reversion (`kiro_pair_reversion`, PSR).

Covers every verification item in Handoff Section A11:
1. Strict No Look-Ahead: daily bars strictly <= D-1 (D-day bar ignored even if injected)
2. Determinism: identical MarketSnapshot + AgentLedgerState -> identical OrderSignal output
3. Shared RiskManager pass-through & SKIP_RISK_MANAGER logging on rejection
4. Exit priority 1 -> 10 conflict resolution when multiple triggers fire in one cycle
5. Early-close day time windows (12:00 ET entry cutoff, 12:30 ET time exit) & +1R breakeven stop ratchet
6. SHA-256 integrity hash registration for both agent code and params.json
"""

from __future__ import annotations

import json
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

from arena.agents.base import compute_strategy_hashes  # noqa: E402
from arena.agents.kiro_pair_reversion_agent import (  # noqa: E402
    KiroPairReversionAgent,
    compute_daily_pair_cache,
    load_kiro_psr_params,
)
from arena.engine.risk_manager import RiskManager  # noqa: E402
from arena.models import (  # noqa: E402
    AgentLedgerState,
    Bar,
    MarketSnapshot,
    NewsHeadline,
    Position,
    Quote,
    TickerIndicators,
)


def _make_correlated_pair_bars(
    sym_a: str,
    sym_b: str,
    n_days: int = 75,
    end_date: date = date(2026, 11, 20),
    laggard_last_9d_drop: float = -0.004,
    corr_break: bool = False,
) -> Dict[str, List[Bar]]:
    """Generate deterministic, realistic correlated daily bars ending strictly on end_date."""
    # Generate trading dates (Mon-Fri) ending on end_date
    dates: List[date] = []
    cur = end_date
    while len(dates) < n_days:
        if cur.weekday() < 5:
            dates.append(cur)
        cur -= timedelta(days=1)
    dates.reverse()

    bars_a: List[Bar] = []
    bars_b: List[Bar] = []
    bars_spy: List[Bar] = []
    px_a, px_b, px_spy = 100.0, 100.0, 500.0

    for i, d in enumerate(dates):
        # Smooth deterministic oscillation so correlation is high (~0.88) and sigma_e ~ 0.007 unless corr_break=True
        wave = 0.016 * math.sin(i * 0.55) + 0.008 * math.cos(i * 0.23)
        idio_a = 0.0055 * math.sin(i * 1.7)
        idio_b = -0.0045 * math.cos(i * 1.3)

        if corr_break:
            ret_a = 0.012 * math.sin(i * 1.9)
            ret_b = 0.012 * math.cos(i * 2.7)
        else:
            ret_b = 0.0005 + wave + idio_b
            ret_a = 0.0005 + wave + idio_a
            # Apply gentle multi-day lag on A over last 9 days without exceeding single-day shock_max
            if i >= n_days - 9:
                ret_a += laggard_last_9d_drop

        ret_spy = 0.0004 + 0.7 * wave
        px_a *= math.exp(ret_a)
        px_b *= math.exp(ret_b)
        px_spy *= math.exp(ret_spy)

        for sym, px, arr in ((sym_a, px_a, bars_a), (sym_b, px_b, bars_b), ("SPY", px_spy, bars_spy)):
            arr.append(
                Bar(
                    ticker=sym,
                    timestamp=f"{d.isoformat()}T21:00:00Z",
                    open=round(px * 0.996, 4),
                    high=round(px * 1.008, 4),
                    low=round(px * 0.992, 4),
                    close=round(px, 4),
                    volume=5_000_000.0,
                )
            )

    return {"SPY": bars_spy, sym_a: bars_a, sym_b: bars_b}


def _make_snapshot_for_pair(
    daily_map: Dict[str, List[Bar]],
    trading_date_et: str = "2026-11-23",
    decision_ts: str = "2026-11-23T16:00:00Z",  # 11:00 EST (inside 10:30-15:00 window)
    trading_day_number: int = 5,
    session_close_et: str = "16:00",
    ko_intraday_mult: float = 0.995,
    pep_intraday_mult: float = 1.003,
    spy_intraday_mult: float = 1.002,
    ko_bar_confirm: bool = True,
    news: List[NewsHeadline] | None = None,
    is_near_close: bool = False,
) -> MarketSnapshot:
    ko_prev = daily_map["KO"][-1].close
    pep_prev = daily_map["PEP"][-1].close
    spy_prev = daily_map["SPY"][-1].close

    ko_px = round(ko_prev * ko_intraday_mult, 4)
    pep_px = round(pep_prev * pep_intraday_mult, 4)
    spy_px = round(spy_prev * spy_intraday_mult, 4)

    # 30-min bars for bar_confirm check (E7)
    if ko_bar_confirm:
        ko_30m = [
            Bar("KO", f"{trading_date_et}T15:00:00Z", ko_px * 0.996, ko_px * 0.999, ko_px * 0.994, ko_px * 0.997, 200_000),
            Bar("KO", f"{trading_date_et}T15:30:00Z", ko_px * 0.997, ko_px * 1.002, ko_px * 0.996, ko_px, 220_000),
        ]
    else:
        ko_30m = [
            Bar("KO", f"{trading_date_et}T15:00:00Z", ko_px * 1.004, ko_px * 1.005, ko_px * 1.001, ko_px * 1.002, 200_000),
            Bar("KO", f"{trading_date_et}T15:30:00Z", ko_px * 1.002, ko_px * 1.003, ko_px * 0.999, ko_px, 220_000),
        ]

    indicators = {}
    quotes = {}
    for sym, px, prev in (("SPY", spy_px, spy_prev), ("KO", ko_px, ko_prev), ("PEP", pep_px, pep_prev)):
        indicators[sym] = TickerIndicators(
            ticker=sym,
            last_price=px,
            prev_close=prev,
            change_pct_1d=round(((px / prev) - 1.0) * 100.0, 2),
            change_pct_5d=0.5,
            sma_10=px,
            sma_20=px,
            sma_30=px,
            rsi_14=50.0,
            atr_14=round(px * 0.015, 2),
            bb_upper_20=px * 1.02,
            bb_mid_20=px,
            bb_lower_20=px * 0.98,
            high_20=px * 1.03,
            low_20=px * 0.97,
        )
        quotes[sym] = Quote(
            ticker=sym,
            timestamp=decision_ts,
            bid_price=round(px * 0.9998, 4),
            ask_price=round(px * 1.0002, 4),
            mid_price=px,
            spread_bps=4.0,
        )

    return MarketSnapshot(
        decision_timestamp=decision_ts,
        trading_date_et=trading_date_et,
        trading_day_number=trading_day_number,
        is_near_market_close=is_near_close,
        session_open_et="09:30",
        session_close_et=session_close_et,
        bars_by_ticker={"KO": ko_30m, "PEP": ko_30m, "SPY": ko_30m},
        quotes_by_ticker=quotes,
        indicators=indicators,
        news=news or [],
        daily_bars_by_ticker=daily_map,
    )


class TestKiroPairReversion(unittest.TestCase):
    def test_no_lookahead_daily_bars_strictly_prior_to_trading_date(self) -> None:
        params = load_kiro_psr_params()
        daily_clean = _make_correlated_pair_bars("KO", "PEP", n_days=75, end_date=date(2026, 11, 20))
        cache_clean = compute_daily_pair_cache(daily_clean, trading_date_et="2026-11-23", params=params)
        self.assertTrue(cache_clean["valid"])
        self.assertEqual(cache_clean["spy"]["last_bar_date"], "2026-11-20")
        self.assertEqual(cache_clean["pairs"]["P01"]["last_bar_date"], "2026-11-20")

        # Inject a D-day (2026-11-23) look-ahead bar with extreme +50% fake move -> MUST be ignored!
        daily_leaky = {k: list(v) for k, v in daily_clean.items()}
        for sym in ("SPY", "KO", "PEP"):
            prev_c = daily_leaky[sym][-1].close
            daily_leaky[sym].append(
                Bar(sym, "2026-11-23T21:00:00Z", prev_c, prev_c * 1.5, prev_c, prev_c * 1.5, 9_000_000.0)
            )
        cache_leaky = compute_daily_pair_cache(daily_leaky, trading_date_et="2026-11-23", params=params)
        self.assertEqual(cache_clean["spy"], cache_leaky["spy"])
        self.assertEqual(cache_clean["pairs"]["P01"], cache_leaky["pairs"]["P01"])

    def test_same_snapshot_determinism(self) -> None:
        daily_map = _make_correlated_pair_bars("KO", "PEP", n_days=75, end_date=date(2026, 11, 20), laggard_last_9d_drop=-0.006)
        snap = _make_snapshot_for_pair(daily_map, ko_intraday_mult=0.996, pep_intraday_mult=1.002, ko_bar_confirm=True)

        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            a1 = KiroPairReversionAgent(state_dir=Path(tmp1))
            a2 = KiroPairReversionAgent(state_dir=Path(tmp2))
            s1 = AgentLedgerState(agent_id="kiro_pair_reversion", name=a1.name, category=a1.category)
            s2 = AgentLedgerState(agent_id="kiro_pair_reversion", name=a2.name, category=a2.category)

            sig1 = a1.decide_batch(snap, s1)
            sig2 = a2.decide_batch(snap, s2)
            self.assertEqual(len(sig1), 1)
            self.assertEqual(sig1[0].action, "BUY")
            self.assertEqual(sig1[0].ticker, "KO")
            self.assertEqual(sig1[0].to_dict(), sig2[0].to_dict())

    def test_risk_manager_integration_and_rejection_logged_as_skip(self) -> None:
        daily_map = _make_correlated_pair_bars("KO", "PEP", n_days=75, end_date=date(2026, 11, 20), laggard_last_9d_drop=-0.006)
        snap = _make_snapshot_for_pair(daily_map, ko_intraday_mult=0.996, pep_intraday_mult=1.002, ko_bar_confirm=True)

        with tempfile.TemporaryDirectory() as tmp:
            agent = KiroPairReversionAgent(state_dir=Path(tmp))
            rm = RiskManager()

            # 1) Normal state -> approved by shared RiskManager with stricter limits
            state_ok = AgentLedgerState(agent_id="kiro_pair_reversion", name=agent.name, category=agent.category)
            sigs = agent.decide_batch(snap, state_ok)
            self.assertEqual(sigs[0].action, "BUY")
            res_ok = rm.evaluate(
                signal=sigs[0],
                state=state_ok,
                quote=snap.quotes_by_ticker["KO"],
                stricter_limits=agent.get_effective_stricter_limits(),
            )
            self.assertTrue(res_ok.approved, f"Expected approval, got: {res_ok.rejection_reason}")
            self.assertLessEqual(res_ok.estimated_notional, state_ok.equity * 0.18 + 1.0)

        # 2) Zero-cash state -> rejected by RiskManager, logged as SKIP_RISK_MANAGER & rolled back
        with tempfile.TemporaryDirectory() as tmp2:
            agent2 = KiroPairReversionAgent(state_dir=Path(tmp2))
            rm = RiskManager()
            state_broke = AgentLedgerState(
                agent_id="kiro_pair_reversion", name=agent2.name, category=agent2.category, cash=0.0, equity=10_000.0
            )
            sigs2 = agent2.decide_batch(snap, state_broke)
            self.assertEqual(sigs2[0].action, "BUY")
            res_rej = rm.evaluate(
                signal=sigs2[0],
                state=state_broke,
                quote=snap.quotes_by_ticker["KO"],
                stricter_limits=agent2.get_effective_stricter_limits(),
            )
            self.assertFalse(res_rej.approved)
            agent2.record_risk_rejection(sigs2[0], res_rej.rejection_reason)
            logs = json.loads((Path(tmp2) / "kiro_psr_cycle_logs.json").read_text(encoding="utf-8"))
            codes = [a["code"] for a in logs[-1]["actions"]]
            self.assertIn("SKIP_RISK_MANAGER", codes)
            psr_st = agent2.load_psr_state()
            self.assertEqual(psr_st["entries_today_count"], 0)
            self.assertNotIn("KO", psr_st["position_meta"])

    def test_exit_priority_1_to_10_conflict_resolution(self) -> None:
        daily_map = _make_correlated_pair_bars("KO", "PEP", n_days=75, end_date=date(2026, 11, 20), laggard_last_9d_drop=-0.010)

        def _run_exit_case(
            trading_day_number: int,
            decision_ts: str,
            news_list: List[NewsHeadline],
            ko_mult: float,
            spy_mult: float,
            custom_daily: Dict[str, List[Bar]] | None = None,
        ) -> str:
            eff_daily = custom_daily or daily_map
            eff_ko_prev = eff_daily["KO"][-1].close
            with tempfile.TemporaryDirectory() as tmp:
                agent = KiroPairReversionAgent(state_dir=Path(tmp))
                psr_st = agent.load_psr_state()
                psr_st["trading_dates_seen"] = [f"2026-11-{10 + i:02d}" for i in range(8)]
                psr_st["position_meta"]["KO"] = {
                    "pair_id": "P01",
                    "leg": "A",
                    "partner": "PEP",
                    "entry_price": eff_ko_prev * 1.03,  # guaranteed underwater
                    "stop_pct": 0.04,
                    "cost_bps": 14.0,
                    "entry_day_idx": 0,  # held >= 7 days
                }
                agent.save_psr_state(psr_st)
                st = AgentLedgerState(agent_id="kiro_pair_reversion", name=agent.name, category=agent.category)
                st.positions["KO"] = Position(
                    ticker="KO",
                    shares=10.0,
                    avg_entry_price=eff_ko_prev * 1.03,
                    current_price=eff_ko_prev * ko_mult,
                    stop_loss=eff_ko_prev * 0.90,
                    take_profit=eff_ko_prev * 1.15,
                    opened_at="2026-11-10T16:00:00Z",
                )
                snap = _make_snapshot_for_pair(
                    eff_daily,
                    trading_date_et="2026-11-23",
                    decision_ts=decision_ts,
                    trading_day_number=trading_day_number,
                    ko_intraday_mult=ko_mult,
                    pep_intraday_mult=1.01,
                    spy_intraday_mult=spy_mult,
                    news=news_list,
                )
                sigs = agent.decide_batch(snap, st)
                self.assertEqual(sigs[0].action, "SELL")
                return sigs[0].reasoning.split(":")[0]

        veto_news = [NewsHeadline("KO faces DOJ probe over pricing", "", "Alpaca", "2026-11-23T14:00:00Z", ["KO"])]

        # Priority 4 (EXIT_FINAL_DAY) beats Priority 5 (NEWS), 6 (Z_STOP), 7 (CRASH), 10 (TIME)
        self.assertEqual(
            _run_exit_case(30, "2026-11-23T20:30:00Z", veto_news, ko_mult=0.97, spy_mult=0.97),
            "EXIT_FINAL_DAY",
        )
        # Priority 5 (EXIT_NEWS_VETO) beats Priority 6 (Z_STOP) & 7 (CRASH)
        self.assertEqual(
            _run_exit_case(15, "2026-11-23T20:30:00Z", veto_news, ko_mult=0.97, spy_mult=0.97),
            "EXIT_NEWS_VETO",
        )
        # Priority 6 (EXIT_Z_STOP) beats Priority 7 (CRASH) & 10 (TIME)
        self.assertEqual(
            _run_exit_case(15, "2026-11-23T20:30:00Z", [], ko_mult=0.96, spy_mult=0.97),
            "EXIT_Z_STOP",
        )
        # Priority 7 (EXIT_CRASH) beats Priority 8 (TP_REVERT) & 10 (TIME) when underwater
        daily_reverted = _make_correlated_pair_bars("KO", "PEP", n_days=75, end_date=date(2026, 11, 20), laggard_last_9d_drop=0.0)
        self.assertEqual(
            _run_exit_case(15, "2026-11-23T20:30:00Z", [], ko_mult=1.00, spy_mult=0.975, custom_daily=daily_reverted),
            "EXIT_CRASH",
        )
        # Priority 8 (EXIT_TP_REVERT) beats Priority 9 (CORR_BREAK) & 10 (TIME)
        daily_broken = _make_correlated_pair_bars("KO", "PEP", n_days=75, end_date=date(2026, 11, 20), corr_break=True)
        self.assertEqual(
            _run_exit_case(15, "2026-11-23T20:30:00Z", [], ko_mult=1.02, spy_mult=1.00, custom_daily=daily_broken),
            "EXIT_TP_REVERT",
        )

    def test_early_close_windows_and_breakeven_ratchet(self) -> None:
        daily_map = _make_correlated_pair_bars("KO", "PEP", n_days=75, end_date=date(2026, 11, 20), laggard_last_9d_drop=-0.0055)
        ko_prev = daily_map["KO"][-1].close

        # 1) Early-close day at 12:30 ET (17:30 UTC): outside entry window (cutoff 12:00 ET) -> SKIP_OUT_OF_WINDOW
        with tempfile.TemporaryDirectory() as tmp:
            agent = KiroPairReversionAgent(state_dir=Path(tmp))
            st = AgentLedgerState(agent_id="kiro_pair_reversion", name=agent.name, category=agent.category)
            snap_late_early = _make_snapshot_for_pair(
                daily_map,
                trading_date_et="2026-11-27",
                decision_ts="2026-11-27T17:30:00Z",  # 12:30 EST on Black Friday (13:00 close)
                session_close_et="13:00",
                ko_bar_confirm=True,
            )
            sigs = agent.decide_batch(snap_late_early, st)
            self.assertEqual(sigs[0].action, "HOLD")
            self.assertIn("SKIP_OUT_OF_WINDOW", sigs[0].reasoning)

        # 2) Early-close day at 12:30 ET with 7-day-old position -> triggers EXIT_TIME AND +1R breakeven ratchet!
        with tempfile.TemporaryDirectory() as tmp2:
            agent2 = KiroPairReversionAgent(state_dir=Path(tmp2))
            psr_st = agent2.load_psr_state()
            psr_st["trading_dates_seen"] = [f"2026-11-{10 + i:02d}" for i in range(8)]
            entry_px = round(ko_prev * 0.95, 4)  # position is up > +1R
            psr_st["position_meta"]["KO"] = {
                "pair_id": "P01",
                "leg": "A",
                "partner": "PEP",
                "entry_price": entry_px,
                "stop_pct": 0.03,
                "cost_bps": 14.0,
                "entry_day_idx": 0,
            }
            agent2.save_psr_state(psr_st)
            st2 = AgentLedgerState(agent_id="kiro_pair_reversion", name=agent2.name, category=agent2.category)
            st2.positions["KO"] = Position(
                ticker="KO",
                shares=10.0,
                avg_entry_price=entry_px,
                current_price=ko_prev,
                stop_loss=round(entry_px * 0.97, 4),
                take_profit=round(entry_px * 1.15, 4),
                opened_at="2026-11-10T16:00:00Z",
            )
            # Keep lz around -2.0 so TP_REVERT (-0.5) doesn't trigger before EXIT_TIME (10)
            snap_ec = _make_snapshot_for_pair(
                daily_map,
                trading_date_et="2026-11-27",
                decision_ts="2026-11-27T17:30:00Z",  # 12:30 EST
                session_close_et="13:00",
                ko_intraday_mult=0.999,
                pep_intraday_mult=1.001,
            )
            sigs2 = agent2.decide_batch(snap_ec, st2)
            # Breakeven stop must have ratcheted up to >= entry * (1 + 14/10000)
            self.assertGreaterEqual(st2.positions["KO"].stop_loss, round(entry_px * 1.0014, 4))
            self.assertEqual(sigs2[0].action, "SELL")
            self.assertTrue(sigs2[0].reasoning.startswith("EXIT_TIME"))

    def test_strategy_sha256_hash_registered(self) -> None:
        hashes = compute_strategy_hashes()
        self.assertIn("kiro_pair_reversion_agent.py", hashes)
        self.assertIn("kiro_pair_reversion_params.json", hashes)
        self.assertEqual(len(hashes["kiro_pair_reversion_agent.py"]), 64)
        self.assertEqual(len(hashes["kiro_pair_reversion_params.json"]), 64)


if __name__ == "__main__":
    unittest.main()
