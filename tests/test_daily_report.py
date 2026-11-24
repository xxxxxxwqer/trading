"""Unit tests for the 3-stage anti-hallucination daily Markdown reporting pipeline."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from arena.config import AGENT_SPECS, INITIAL_CASH  # noqa: E402
from arena.dashboard.builder import DashboardBuilder  # noqa: E402
from arena.models import (  # noqa: E402
    AgentLedgerState,
    Bar,
    DailyJournalEntry,
    EquityPoint,
    MarketSnapshot,
    NewsHeadline,
    Position,
    Quote,
    RejectionRecord,
    TickerIndicators,
    TradeRecord,
)
from arena.reporting.daily_report import (  # noqa: E402
    build_report_payload,
    build_smart_market_summary,
    classify_agent_group,
    generate_and_save_daily_report,
    render_deterministic_markdown_report,
    validate_report_markdown,
)


def _make_agent_states(num_days: int = 1) -> list[AgentLedgerState]:
    """Create a realistic 13-agent roster with controlled equity, trades, positions, and rejections."""
    from datetime import date, timedelta

    states: list[AgentLedgerState] = []
    base = date(2026, 11, 10)
    dates = [(base + timedelta(days=d)).isoformat() for d in range(1, num_days + 1)]
    last_date = dates[-1]

    for idx, spec in enumerate(AGENT_SPECS):
        # Deterministic variation so SPY, Random, Coded, LLM, Style, and Claude RMP have distinct figures
        if spec.agent_id == "ctrl_spy":
            daily_step = 0.005  # +0.5%/day
        elif spec.agent_id == "ctrl_random":
            daily_step = -0.002  # -0.2%/day
        elif spec.agent_id == "claude_rmp":
            daily_step = 0.008  # +0.8%/day
        else:
            daily_step = 0.003 - (idx * 0.0005)

        eq = INITIAL_CASH
        peak = INITIAL_CASH
        curve: list[EquityPoint] = []
        journal: list[DailyJournalEntry] = []
        prev_eq = INITIAL_CASH
        for d_idx, d_str in enumerate(dates, start=1):
            start_eq = eq
            eq = round(eq * (1.0 + daily_step), 2)
            peak = max(peak, eq)
            dd = round(((eq - peak) / peak) * 100.0, 2)
            dpnl = round(eq - start_eq, 2)
            dpct = round((dpnl / start_eq) * 100.0, 2)
            curve.append(
                EquityPoint(
                    timestamp=f"{d_str}T20:30:00Z",
                    trading_date_et=d_str,
                    equity=eq,
                    cash=round(eq * 0.6, 2),
                    positions_value=round(eq * 0.4, 2),
                    drawdown_pct=dd,
                    daily_return_pct=dpct,
                )
            )
            journal.append(
                DailyJournalEntry(
                    trading_date_et=d_str,
                    trading_day_number=d_idx,
                    start_equity=start_eq,
                    end_equity=eq,
                    daily_pnl=dpnl,
                    daily_pnl_pct=dpct,
                    trades_count=1 if d_idx == num_days else 0,
                    rejections_count=0,
                    summary_text=f"Test day {d_idx}",
                )
            )
            prev_eq = start_eq

        st = AgentLedgerState(
            agent_id=spec.agent_id,
            name=spec.name,
            category=spec.category,
            status="ACTIVE",
            status_reason="Trading active",
            initial_cash=INITIAL_CASH,
            cash=round(eq * 0.6, 2),
            equity=eq,
            peak_equity=peak,
            max_drawdown_pct=min(0.0, round(((eq - peak) / peak) * 100.0, 2)),
            current_drawdown_pct=round(((eq - peak) / peak) * 100.0, 2),
            current_trading_date_et=last_date,
            day_start_equity=prev_eq,
            daily_pnl_pct=round(((eq - prev_eq) / prev_eq) * 100.0, 2),
            equity_curve=curve,
            daily_journal=journal,
        )

        # Give claude_rmp a closed winning trade + an open position so Average R and open-position distances are tested
        if spec.agent_id == "claude_rmp":
            st.trades = [
                TradeRecord(
                    trade_id="rmp-buy-1",
                    agent_id=spec.agent_id,
                    side="BUY",
                    ticker="NVDA",
                    shares=10.0,
                    decision_timestamp=f"{last_date}T15:00:00Z",
                    fill_timestamp=f"{last_date}T15:01:00Z",
                    reference_price=100.0,
                    fill_price=100.10,
                    spread_cost_dollars=0.50,
                    slippage_cost_dollars=0.50,
                    commission_dollars=0.0,
                    notional_dollars=1001.0,
                    stop_loss=95.10,
                    take_profit=110.10,
                    reasoning="ENTRY_RMP pullback inside [1.0, 3.5] ATR",
                    confidence=0.80,
                ),
                TradeRecord(
                    trade_id="rmp-sell-1",
                    agent_id=spec.agent_id,
                    side="SELL",
                    ticker="NVDA",
                    shares=10.0,
                    decision_timestamp=f"{last_date}T19:00:00Z",
                    fill_timestamp=f"{last_date}T19:01:00Z",
                    reference_price=110.20,
                    fill_price=110.10,
                    spread_cost_dollars=0.50,
                    slippage_cost_dollars=0.50,
                    commission_dollars=0.0,
                    notional_dollars=1101.0,
                    stop_loss=None,
                    take_profit=None,
                    realized_pnl=100.0,
                    realized_pnl_pct=9.99,
                    exit_reason="EXIT_TRAIL",
                    reasoning="EXIT_TRAIL locked gain",
                    confidence=0.85,
                ),
            ]
            st.positions["AAPL"] = Position(
                ticker="AAPL",
                shares=5.0,
                avg_entry_price=200.0,
                current_price=204.0,
                market_value=1020.0,
                unrealized_pnl=20.0,
                unrealized_pnl_pct=2.0,
                stop_loss=196.0,
                take_profit=216.0,
                opened_at=f"{last_date}T16:00:00Z",
                entry_reasoning="Pullback entry",
            )
            st.rejections = [
                RejectionRecord(
                    timestamp=f"{last_date}T17:00:00Z",
                    agent_id=spec.agent_id,
                    action="BUY",
                    ticker="MSFT",
                    reason="Sector cap reached (2/2)",
                )
            ]

        states.append(st)
    return states


def _make_snapshot(trading_date_et: str = "2026-11-11") -> MarketSnapshot:
    indicators = {}
    quotes = {}
    daily_bars = {}
    for sym, px, chg in (
        ("SPY", 590.0, 0.50),
        ("QQQ", 505.0, 0.85),
        ("TLT", 92.0, -0.30),
        ("GLD", 245.0, 0.15),
        ("NVDA", 110.1, 2.40),
        ("AAPL", 204.0, 1.10),
        ("MSFT", 420.0, -0.40),
    ):
        indicators[sym] = TickerIndicators(
            ticker=sym,
            last_price=px,
            prev_close=round(px / (1.0 + chg / 100.0), 2),
            change_pct_1d=chg,
            change_pct_5d=chg * 2.0,
            sma_10=px * 0.99,
            sma_20=px * 0.98,
            sma_30=px * 0.97,
            rsi_14=62.0 if chg > 0 else 41.0,
            atr_14=round(px * 0.015, 2),
            bb_upper_20=px * 1.03,
            bb_mid_20=px * 0.98,
            bb_lower_20=px * 0.93,
            high_20=px * 1.02,
            low_20=px * 0.94,
            opening_range_high=px * 0.995,
            opening_range_low=px * 0.985,
            volume_ratio=1.35 if sym == "NVDA" else 1.05,
            relative_strength_vs_spy=round(chg - 0.50, 2),
        )
        quotes[sym] = Quote(
            ticker=sym,
            timestamp=f"{trading_date_et}T20:30:00Z",
            bid_price=round(px - 0.05, 2),
            ask_price=round(px + 0.05, 2),
            mid_price=px,
            spread_bps=1.7,
        )
        daily_bars[sym] = [
            Bar(
                ticker=sym,
                timestamp=f"2026-09-{(i % 28) + 1:02d}T20:00:00Z",
                open=px * 0.95,
                high=px * 0.97,
                low=px * 0.94,
                close=px * (0.94 + i * 0.0007),
                volume=1_000_000.0,
            )
            for i in range(70)
        ]

    return MarketSnapshot(
        decision_timestamp=f"{trading_date_et}T20:30:00Z",
        trading_date_et=trading_date_et,
        trading_day_number=1,
        is_near_market_close=True,
        session_open_et="09:30",
        session_close_et="16:00",
        bars_by_ticker={},
        quotes_by_ticker=quotes,
        indicators=indicators,
        news=[
            NewsHeadline(
                headline="NVIDIA posts strong AI datacenter shipment update",
                summary="Shipments rise",
                source="AlpacaNews",
                created_at=f"{trading_date_et}T14:00:00Z",
                symbols=["NVDA"],
            )
        ],
        regime_summary="Risk-On uptrend",
        daily_bars_by_ticker=daily_bars,
    )


class TestDailyReportPipeline(unittest.TestCase):
    def test_four_strategy_groups_cover_exact_13_agents(self) -> None:
        counts = {}
        for spec in AGENT_SPECS:
            grp = classify_agent_group(spec.agent_id, spec.category)
            counts[grp] = counts.get(grp, 0) + 1
        self.assertEqual(len(AGENT_SPECS), 13)
        self.assertEqual(counts["코딩 기반 (5)"], 5)
        self.assertEqual(counts["Gemini Flash LLM (4)"], 4)
        self.assertEqual(counts["공시 스타일 학습 (2)"], 2)
        self.assertEqual(counts["SPY / 랜덤 (2)"], 2)

    def test_smart_market_summary_filters_to_macro_and_active_tickers(self) -> None:
        states = _make_agent_states(num_days=1)
        snap = _make_snapshot("2026-11-11")
        summary = build_smart_market_summary(snap, states)

        self.assertEqual(set(summary["macro_assets"].keys()), {"SPY", "QQQ", "TLT", "GLD"})
        # NVDA was traded today and AAPL is currently held; MSFT was neither traded nor held
        self.assertIn("NVDA", summary["active_ticker_snapshots"])
        self.assertIn("AAPL", summary["active_ticker_snapshots"])
        self.assertNotIn("MSFT", summary["active_ticker_snapshots"])
        self.assertEqual(len(summary["headlines"]), 1)

    def test_precomputed_metrics_accuracy_and_average_r(self) -> None:
        states = _make_agent_states(num_days=1)
        snap = _make_snapshot("2026-11-11")
        summary = build_smart_market_summary(snap, states)

        payload = build_report_payload(
            agent_states=states,
            competition_state={"status": "LIVE"},
            trading_date_et="2026-11-11",
            trading_day_number=1,
            market_summary=summary,
        )

        self.assertEqual(payload["metadata"]["total_agents"], 13)
        self.assertFalse(payload["metadata"]["is_final_day"])
        self.assertIsNone(payload["final_10day_stats"])
        self.assertEqual(payload["metadata"]["cycles_run_today"], 1)

        # Verify claude_rmp calculations:
        # BUY 10 @ 100.10 with stop 95.10 -> risk = $5/sh * 10 = $50. Realized PnL = $100 -> Average R = +2.00R
        self.assertEqual(payload["claude_rmp_telemetry"]["average_r"], 2.0)

        rmp_row = next(r for r in payload["leaderboard"] if r["agent_id"] == "claude_rmp")
        spy_row = next(r for r in payload["leaderboard"] if r["agent_id"] == "ctrl_spy")
        rnd_row = next(r for r in payload["leaderboard"] if r["agent_id"] == "ctrl_random")

        self.assertEqual(rmp_row["profit_factor_str"], "∞")
        self.assertEqual(spy_row["profit_factor_str"], "N/A")
        self.assertAlmostEqual(rmp_row["vs_spy_pp"], round(rmp_row["cum_return_pct"] - spy_row["cum_return_pct"], 2))
        self.assertAlmostEqual(rmp_row["vs_random_pp"], round(rmp_row["cum_return_pct"] - rnd_row["cum_return_pct"], 2))

        # Open position AAPL: price 204, stop 196 -> (196-204)/204 = -3.92%, TP 216 -> +5.88%
        aapl_pos = next(p for p in payload["open_positions"] if p["ticker"] == "AAPL")
        self.assertAlmostEqual(aapl_pos["stop_dist_pct"], -3.92, places=2)
        self.assertAlmostEqual(aapl_pos["tp_dist_pct"], 5.88, places=2)

    def test_validator_enforces_section8_and_no_tuning_guard(self) -> None:
        states = _make_agent_states(num_days=1)
        payload = build_report_payload(
            agent_states=states,
            competition_state={"status": "LIVE"},
            trading_date_et="2026-11-11",
            trading_day_number=1,
            market_summary={},
        )
        md_day1 = render_deterministic_markdown_report(payload)
        ok, errs = validate_report_markdown(md_day1, states, is_final_day=False)
        self.assertTrue(ok, f"Day 1 deterministic report failed validation: {errs}")
        self.assertNotIn("## 8.", md_day1)

        # Injecting Section 8 on Day 1 must fail
        ok_bad_sec8, _ = validate_report_markdown(md_day1 + "\n## 8. Extra\n", states, is_final_day=False)
        self.assertFalse(ok_bad_sec8)

        # Injecting strategy tuning advice must fail
        ok_tuning, _ = validate_report_markdown(md_day1 + "\n손절폭을 넓혀야 합니다.\n", states, is_final_day=False)
        self.assertFalse(ok_tuning)

    def test_day10_final_report_includes_student_t_section8(self) -> None:
        from arena.config import COMPETITION_TRADING_DAYS

        states = _make_agent_states(num_days=COMPETITION_TRADING_DAYS)
        last_date = states[0].current_trading_date_et
        payload = build_report_payload(
            agent_states=states,
            competition_state={"status": "COMPLETED"},
            trading_date_et=last_date,
            trading_day_number=COMPETITION_TRADING_DAYS,
            market_summary={},
        )
        self.assertTrue(payload["metadata"]["is_final_day"])
        self.assertIsNotNone(payload["final_10day_stats"])
        self.assertEqual(payload["final_10day_stats"]["n_trading_days"], COMPETITION_TRADING_DAYS)

        md_final = render_deterministic_markdown_report(payload)
        ok, errs = validate_report_markdown(md_final, states, is_final_day=True)
        self.assertTrue(ok, f"Final-day deterministic report failed validation: {errs}")
        self.assertIn("## 8. 통계적 최종 평가: 운(Luck) vs 실력(Skill)", md_final)
        self.assertIn("95% 신뢰구간", md_final)

    def test_generate_save_and_render_dashboard_reports_page(self) -> None:
        states = _make_agent_states(num_days=1)
        snap = _make_snapshot("2026-11-11")
        summary = build_smart_market_summary(snap, states)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "state"
            reports_dir = tmp_path / "reports"
            docs_dir = tmp_path / "docs"

            res = generate_and_save_daily_report(
                agent_states=states,
                competition_state={"status": "LIVE"},
                trading_date_et="2026-11-11",
                trading_day_number=1,
                market_summary=summary,
                state_dir=state_dir,
                reports_dir=reports_dir,
                use_llm=False,
            )
            self.assertTrue(res["post_check_passed"])
            self.assertTrue(Path(res["report_path"]).exists())
            self.assertTrue((reports_dir / "LATEST_REPORT.md").exists())
            log_data = json.loads((state_dir / "daily_reports_log.json").read_text(encoding="utf-8"))
            self.assertEqual(len(log_data), 1)
            self.assertEqual(log_data[0]["filename"], "2026-11-11_day01.md")

            # Verify DashboardBuilder creates docs/reports.html with rendered report content
            DashboardBuilder().build_site(
                agent_states=states,
                competition_state={"status": "LIVE", "trading_days_completed": 1},
                bug_fixes=[],
                output_dir=docs_dir,
                state_dir=state_dir,
                reports_dir=reports_dir,
            )
            reports_html = (docs_dir / "reports.html").read_text(encoding="utf-8")
            from arena.config import COMPETITION_TRADING_DAYS

            self.assertIn(f"Day 1/{COMPETITION_TRADING_DAYS} Daily Report", reports_html)
            self.assertIn("2026-11-11_day01.md", reports_html)
            self.assertIn("claude_rmp", reports_html)


if __name__ == "__main__":
    unittest.main()
