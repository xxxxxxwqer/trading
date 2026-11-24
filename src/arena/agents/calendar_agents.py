"""Part B-A: Time-of-Day & Calendar Effect Agents (#16–#20).

- #16 overnight_drift       : Close (15:30 ET) -> Open (first cycle) SPY/QQQ overnight premium
- #17 eod_loser_overnight   : Intraday <= -2.5% non-news stock losers bought at 15:30 ET -> sold >= 10:30 ET next day
- #18 intraday_momentum     : Gao et al. (2018) first half-hour SPY return -> late-day 15:00->15:30 ET momentum
- #19 turn_of_month         : Last trading day of month through 3rd trading day of next month in SPY/QQQ
- #20 gap_down_fill         : -1.5% to -4.0% non-news gap-down + green first 30-min bar -> target prev_close, flat EOD
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Dict, List

from ..config import LIQUID_LARGE_CAP_STOCKS
from ..data.alpaca_client import is_us_trading_day
from ..models import AgentLedgerState, MarketSnapshot, OrderSignal
from .part_b_common import (
    BasePartBAgent,
    get_last_price,
    get_spread_bps,
    has_recent_news_veto,
    intraday_bars_today,
    is_early_close,
    load_fundamentals_map,
    parse_hhmm_minutes,
    snapshot_et_minutes,
    strict_prior_daily_bars,
)


def _is_turn_of_month_day(trading_date_et: str, last_n: int = 1, first_n: int = 3) -> bool:
    """Return True if trading_date_et is among the last `last_n` or first `first_n` US trading days of its month."""
    try:
        d = date.fromisoformat(trading_date_et)
    except Exception:
        return False

    # Collect all US trading days in d's month
    cur = date(d.year, d.month, 1)
    month_tdays: List[date] = []
    while cur.month == d.month:
        if is_us_trading_day(cur):
            month_tdays.append(cur)
        cur += timedelta(days=1)

    if d not in month_tdays:
        return False
    idx = month_tdays.index(d)
    return (idx < first_n) or (idx >= len(month_tdays) - last_n)


class OvernightDriftAgent(BasePartBAgent):
    """#16 — Buys SPY & QQQ at final afternoon cycle (15:30 / 12:30 ET) and sells next morning open."""

    DEFAULT_AGENT_ID = "overnight_drift"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mins = snapshot_et_minutes(snapshot)
        ec = is_early_close(snapshot)
        entry_min = parse_hhmm_minutes(
            str(self.cfg.get("entry_et_early" if ec else "entry_et_normal", "12:30" if ec else "15:30")),
            12 * 60 + 30 if ec else 15 * 60 + 30,
        )
        exit_before = parse_hhmm_minutes(str(self.cfg.get("exit_before_et", "10:00")), 10 * 60)

        # Morning unwind: sell everything opened on an earlier trading day
        if mins <= exit_before:
            sells = [
                OrderSignal(
                    agent_id=self.agent_id,
                    action="SELL",
                    ticker=sym,
                    stop_loss=None,
                    take_profit=None,
                    confidence=0.90,
                    reasoning="EXIT_OPEN_UNWIND: closing overnight SPY/QQQ drift position at market open",
                    requested_shares=pos.shares,
                    decision_timestamp=snapshot.decision_timestamp,
                )
                for sym, pos in sorted(state.positions.items())
                if pos.shares > 0
            ]
            return sells or [self.hold_signal(snapshot, reasoning="HOLD_OVERNIGHT: waiting for afternoon close window")]

        # Late-afternoon entry
        if mins >= entry_min or snapshot.is_near_market_close:
            w = float(self.cfg.get("weight_per_ticker", 0.195))
            targets = {sym: w for sym in self.cfg.get("tickers", ["SPY", "QQQ"])}
            return self.build_rebalance_signals(
                snapshot,
                state,
                target_weights=targets,
                stop_pct=float(self.cfg.get("stop_pct", 0.03)),
                reason_prefix="OVERNIGHT_DRIFT",
            )

        return [self.hold_signal(snapshot, reasoning="HOLD_OVERNIGHT: intraday cash outside close->open window")]


class EODLoserOvernightAgent(BasePartBAgent):
    """#17 — Buys non-news stocks down >= 2.5% intraday at 15:30 ET; unwinds by 10:30–11:00 ET next day."""

    DEFAULT_AGENT_ID = "eod_loser_overnight"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mins = snapshot_et_minutes(snapshot)
        ec = is_early_close(snapshot)
        entry_min = parse_hhmm_minutes(
            str(self.cfg.get("entry_et_early" if ec else "entry_et_normal", "12:30" if ec else "15:30")),
            12 * 60 + 30 if ec else 15 * 60 + 30,
        )
        exit_after = parse_hhmm_minutes(str(self.cfg.get("exit_after_et", "10:30")), 10 * 60 + 30)
        tdate = snapshot.trading_date_et
        mem = self.load_mem()
        entry_dates: Dict[str, str] = dict(mem.get("entry_dates") or {})

        # Unwind any position from a previous trading day once time >= 10:30 ET
        sells: List[OrderSignal] = []
        if mins >= exit_after:
            for sym, pos in sorted(state.positions.items()):
                if pos.shares > 0 and entry_dates.get(sym, "") != tdate:
                    sells.append(
                        OrderSignal(
                            agent_id=self.agent_id,
                            action="SELL",
                            ticker=sym,
                            stop_loss=None,
                            take_profit=None,
                            confidence=0.85,
                            reasoning="EXIT_MORNING_UNWIND: closing prior-day EOD loser reversal position",
                            requested_shares=pos.shares,
                            decision_timestamp=snapshot.decision_timestamp,
                        )
                    )
                    entry_dates.pop(sym, None)
        if sells:
            mem["entry_dates"] = entry_dates
            self.save_mem(mem)
            return sells

        # Late-day scan for intraday losers <= -2.5% with no veto news
        if mins >= entry_min or snapshot.is_near_market_close:
            drop_thresh = float(self.cfg.get("min_intraday_drop_pct", -0.025))
            max_spr = float(self.cfg.get("max_spread_bps", 15.0))
            max_pos = int(self.cfg.get("max_positions", 3))
            max_sec = int(self.cfg.get("max_per_sector", 1))
            fmap = load_fundamentals_map()

            cands = []
            for sym in sorted(LIQUID_LARGE_CAP_STOCKS):
                if sym in state.positions and state.positions[sym].shares > 0:
                    continue
                prior = strict_prior_daily_bars(snapshot, sym)
                prev_c = float(prior[-1].close) if prior else float((snapshot.indicators.get(sym).prev_close) if snapshot.indicators.get(sym) else 0.0)
                last_px = get_last_price(snapshot, sym)
                if prev_c <= 0 or last_px <= 0:
                    continue
                ret_day = (last_px / prev_c) - 1.0
                if ret_day > drop_thresh:
                    continue
                if get_spread_bps(snapshot, sym) > max_spr:
                    continue
                if has_recent_news_veto(snapshot, sym, self.common_veto_keywords):
                    continue
                sec = str((fmap.get(sym) or {}).get("sector", "Unknown"))
                cands.append((ret_day, sym, last_px, sec))

            cands.sort(key=lambda x: (x[0], x[1]))  # worst losers first
            sec_cnt: Dict[str, int] = {}
            chosen: Dict[str, float] = {}
            w = float(self.cfg.get("weight_per_ticker", 0.19))
            for ret_day, sym, _px, sec in cands:
                if len(chosen) >= max_pos:
                    break
                if sec_cnt.get(sec, 0) >= max_sec:
                    continue
                chosen[sym] = w
                sec_cnt[sec] = sec_cnt.get(sec, 0) + 1
                entry_dates[sym] = tdate

            if chosen:
                mem["entry_dates"] = entry_dates
                self.save_mem(mem)
                return self.build_rebalance_signals(
                    snapshot,
                    state,
                    target_weights={**{s: w for s in state.positions if state.positions[s].shares > 0}, **chosen},
                    stop_pct=float(self.cfg.get("stop_pct", 0.04)),
                    take_profit_pct=float(self.cfg.get("tp_pct", 0.04)),
                    reason_prefix="EOD_LOSER",
                )

        return [self.hold_signal(snapshot, reasoning="HOLD_EOD_LOSER: waiting for EOD loser setup or morning unwind")]


class IntradayMomentumAgent(BasePartBAgent):
    """#18 — Gao et al. (2018) intraday momentum: positive overnight+first-30m SPY move -> buy at 15:00 ET, flat EOD."""

    DEFAULT_AGENT_ID = "intraday_momentum"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mins = snapshot_et_minutes(snapshot)
        ec = is_early_close(snapshot)
        entry_min = parse_hhmm_minutes(
            str(self.cfg.get("entry_et_early" if ec else "entry_et_normal", "12:00" if ec else "15:00")),
            12 * 60 if ec else 15 * 60,
        )
        # Enter strictly in the 15:00–15:24 ET window (12:00–12:24 ET early close); flatten_eod=True closes at 15:30/12:30 ET
        if not (entry_min <= mins < entry_min + 25):
            return [self.hold_signal(snapshot, reasoning="HOLD_INTRADAY_MOM: outside late-day entry window")]

        spy_prior = strict_prior_daily_bars(snapshot, "SPY")
        spy_prev = float(spy_prior[-1].close) if spy_prior else float(snapshot.indicators["SPY"].prev_close if "SPY" in snapshot.indicators else 0.0)
        spy_today = intraday_bars_today(snapshot, "SPY")
        if spy_prev <= 0 or not spy_today:
            return [self.hold_signal(snapshot, reasoning="HOLD_INTRADAY_MOM: insufficient morning SPY bar")]

        first_bar_close = float(spy_today[0].close)
        r_first = (first_bar_close / spy_prev) - 1.0
        min_ret = float(self.cfg.get("min_morning_return_pct", 0.0015))
        if r_first < min_ret:
            return [self.hold_signal(snapshot, reasoning=f"HOLD_INTRADAY_MOM: morning SPY return {r_first*100:.2f}% < +{min_ret*100:.2f}%")]

        w = float(self.cfg.get("weight_per_ticker", 0.195))
        targets = {"SPY": w}
        # Also include QQQ if QQQ first bar also positive
        qqq_prior = strict_prior_daily_bars(snapshot, "QQQ")
        qqq_today = intraday_bars_today(snapshot, "QQQ")
        if qqq_prior and qqq_today and qqq_prior[-1].close > 0:
            if (qqq_today[0].close / qqq_prior[-1].close - 1.0) >= min_ret:
                targets["QQQ"] = w

        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.015)),
            reason_prefix="INTRADAY_MOM",
        )


class TurnOfMonthAgent(BasePartBAgent):
    """#19 — Holds SPY & QQQ only during Turn-of-Month window (last 1 trading day + first 3 trading days)."""

    DEFAULT_AGENT_ID = "turn_of_month"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        in_window = _is_turn_of_month_day(
            snapshot.trading_date_et,
            last_n=int(self.cfg.get("entry_last_n_days_of_month", 1)),
            first_n=int(self.cfg.get("hold_first_n_days_of_next_month", 3)),
        )
        if in_window:
            w = float(self.cfg.get("weight_per_ticker", 0.195))
            targets = {sym: w for sym in self.cfg.get("tickers", ["SPY", "QQQ"])}
            return self.build_rebalance_signals(
                snapshot,
                state,
                target_weights=targets,
                stop_pct=float(self.cfg.get("stop_pct", 0.045)),
                reason_prefix="TURN_OF_MONTH",
            )
        # Outside window -> cash
        if state.positions:
            return self.build_rebalance_signals(
                snapshot,
                state,
                target_weights={},
                stop_pct=float(self.cfg.get("stop_pct", 0.045)),
                reason_prefix="TOM_END",
            )
        return [self.hold_signal(snapshot, reasoning="HOLD_TOM: outside turn-of-month calendar window (100% cash)")]


class GapDownFillAgent(BasePartBAgent):
    """#20 — Buys -1.5% to -4.0% non-news morning gap-downs confirmed by a green first 30-min bar; targets prev_close."""

    DEFAULT_AGENT_ID = "gap_down_fill"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mins = snapshot_et_minutes(snapshot)
        w_start = parse_hhmm_minutes(str(self.cfg.get("window_start_et", "10:00")), 10 * 60)
        w_end = parse_hhmm_minutes(str(self.cfg.get("window_end_et", "11:30")), 11 * 60 + 30)
        if not (w_start <= mins <= w_end):
            return [self.hold_signal(snapshot, reasoning="HOLD_GAP_FILL: outside morning gap-fill window")]

        max_pos = int(self.cfg.get("max_positions", 3))
        open_cnt = sum(1 for p in state.positions.values() if p.shares > 0)
        if open_cnt >= max_pos:
            return [self.hold_signal(snapshot, reasoning="HOLD_GAP_FILL: max intraday positions reached")]

        g_min = float(self.cfg.get("gap_min_pct", -0.040))
        g_max = float(self.cfg.get("gap_max_pct", -0.015))
        s_min = float(self.cfg.get("stop_pct_min", 0.015))
        s_max = float(self.cfg.get("stop_pct_max", 0.040))
        w = float(self.cfg.get("weight_per_ticker", 0.19))
        equity = float(state.equity) if state.equity > 0 else 10_000.0

        cands = []
        for sym in sorted(LIQUID_LARGE_CAP_STOCKS):
            if sym in state.positions and state.positions[sym].shares > 0:
                continue
            prior = strict_prior_daily_bars(snapshot, sym)
            prev_c = float(prior[-1].close) if prior else float(snapshot.indicators[sym].prev_close if sym in snapshot.indicators else 0.0)
            today_bars = intraday_bars_today(snapshot, sym)
            if prev_c <= 0 or not today_bars:
                continue
            first_bar = today_bars[0]
            gap_pct = (float(first_bar.open) / prev_c) - 1.0
            if not (g_min <= gap_pct <= g_max):
                continue
            if float(first_bar.close) <= float(first_bar.open):
                continue  # require green first 30-min bar
            last_px = get_last_price(snapshot, sym, fallback=float(first_bar.close))
            if last_px <= 0 or last_px >= prev_c * 0.996:
                continue  # gap already filled
            if has_recent_news_veto(snapshot, sym, self.common_veto_keywords):
                continue
            bounce_strength = (float(first_bar.close) - float(first_bar.open)) / float(first_bar.open)
            cands.append((bounce_strength, sym, last_px, prev_c, float(first_bar.low)))

        cands.sort(key=lambda x: (-x[0], x[1]))
        signals: List[OrderSignal] = []
        for bounce, sym, px, prev_c, bar_low in cands[: max_pos - open_cnt]:
            raw_stop_pct = max(s_min, min(s_max, (px - bar_low * 0.995) / px))
            stop_px = round(px * (1.0 - raw_stop_pct), 4)
            tp_px = round(prev_c, 4)
            shares = math.floor(((equity * w) / px) * 10000.0) / 10000.0
            if shares <= 0:
                continue
            signals.append(
                OrderSignal(
                    agent_id=self.agent_id,
                    action="BUY",
                    ticker=sym,
                    stop_loss=stop_px,
                    take_profit=tp_px,
                    confidence=0.72,
                    reasoning=f"ENTRY_GAP_FILL: gap bounce={bounce*100:.2f}%, target prev_close=${tp_px:.2f}",
                    requested_shares=shares,
                    decision_timestamp=snapshot.decision_timestamp,
                )
            )
        return signals or [self.hold_signal(snapshot, reasoning="HOLD_GAP_FILL: no valid non-news gap-down setups")]
