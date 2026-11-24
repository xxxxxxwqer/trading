"""Part B-E: Intraday Microstructure & Volatility Squeeze Agents (#34–#35).

- #34 vwap_reversion   : Range-bound SPY (|intraday| <= 0.75%); buys stocks below session VWAP - 2*sigma,
                         exits at VWAP reversion & flattens EOD.
- #35 squeeze_breakout : D-1 daily Bollinger Band width in bottom 20% of 60d range -> buys intraday 30m
                         breakout above BB upper with Volume Ratio >= 1.2; 5-day time exit.
"""

from __future__ import annotations

import math
from typing import Dict, List

from ..config import LIQUID_LARGE_CAP_STOCKS
from ..models import AgentLedgerState, MarketSnapshot, OrderSignal
from .claude_rmp_agent import compute_wilder_atr
from .part_b_common import (
    BasePartBAgent,
    compute_bb_width,
    compute_session_vwap_and_std,
    get_last_price,
    has_recent_news_veto,
    intraday_bars_today,
    is_early_close,
    parse_hhmm_minutes,
    snapshot_et_minutes,
    strict_prior_daily_bars,
)


class VWAPReversionAgent(BasePartBAgent):
    """#34 — Intraday VWAP -2σ mean reversion on range-bound SPY days (flat by close)."""

    DEFAULT_AGENT_ID = "vwap_reversion"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        signals: List[OrderSignal] = []
        exiting: set[str] = set()

        # 1. Check VWAP reversion exit on existing open positions
        for sym, pos in sorted(state.positions.items()):
            if pos.shares <= 0:
                continue
            today_bars = intraday_bars_today(snapshot, sym)
            vwap, _sd = compute_session_vwap_and_std(today_bars)
            px = get_last_price(snapshot, sym, fallback=float(pos.current_price))
            if vwap is not None and px >= vwap:
                exiting.add(sym)
                signals.append(
                    OrderSignal(
                        agent_id=self.agent_id,
                        action="SELL",
                        ticker=sym,
                        stop_loss=None,
                        take_profit=None,
                        confidence=0.85,
                        reasoning=f"EXIT_VWAP_REVERT: price ${px:.2f} reverted to session VWAP ${vwap:.2f}",
                        requested_shares=pos.shares,
                        decision_timestamp=snapshot.decision_timestamp,
                    )
                )

        # 2. Entry check only inside range-bound intraday window
        mins = snapshot_et_minutes(snapshot)
        ec = is_early_close(snapshot)
        w_start = parse_hhmm_minutes(str(self.cfg.get("window_start_et", "10:30")), 10 * 60 + 30)
        w_end = parse_hhmm_minutes(
            str(self.cfg.get("window_end_early_et" if ec else "window_end_normal_et", "11:30" if ec else "14:30")),
            11 * 60 + 30 if ec else 14 * 60 + 30,
        )
        if not (w_start <= mins <= w_end):
            return signals or [self.hold_signal(snapshot, reasoning="HOLD_VWAP: outside intraday entry window")]

        # SPY range-bound filter
        spy_prior = strict_prior_daily_bars(snapshot, "SPY")
        spy_prev = float(spy_prior[-1].close) if spy_prior else float(snapshot.indicators["SPY"].prev_close if "SPY" in snapshot.indicators else 0.0)
        spy_now = get_last_price(snapshot, "SPY", fallback=spy_prev)
        spy_move = abs((spy_now / spy_prev) - 1.0) if spy_prev > 0 else 0.0
        if spy_move > float(self.cfg.get("spy_max_abs_intraday", 0.0075)):
            return signals or [self.hold_signal(snapshot, reasoning=f"HOLD_VWAP: SPY trending ({spy_move*100:.2f}% > 0.75%)")]

        open_after = sum(1 for s, p in state.positions.items() if p.shares > 0 and s not in exiting)
        max_pos = int(self.cfg.get("max_positions", 3))
        if open_after >= max_pos:
            return signals or [self.hold_signal(snapshot, reasoning="HOLD_VWAP: max intraday positions reached")]

        sig_th = float(self.cfg.get("sigma_threshold", -2.0))
        min_sig_pct = float(self.cfg.get("min_sigma_pct", 0.0025))
        stop_mult = float(self.cfg.get("stop_sigma_mult", 2.0))
        s_min = float(self.cfg.get("stop_pct_min", 0.015))
        s_max = float(self.cfg.get("stop_pct_max", 0.040))
        w = float(self.cfg.get("weight_per_ticker", 0.19))
        equity = float(state.equity) if state.equity > 0 else 10_000.0

        cands = []
        for sym in sorted(LIQUID_LARGE_CAP_STOCKS):
            if sym in state.positions and state.positions[sym].shares > 0:
                continue
            bars_t = intraday_bars_today(snapshot, sym)
            if len(bars_t) < 2:
                continue
            vwap, sd = compute_session_vwap_and_std(bars_t)
            px = get_last_price(snapshot, sym)
            if vwap is None or sd is None or vwap <= 0 or px <= 0:
                continue
            if (sd / vwap) < min_sig_pct:
                continue
            z_vwap = (px - vwap) / sd
            if z_vwap > sig_th:
                continue
            if has_recent_news_veto(snapshot, sym, self.common_veto_keywords):
                continue
            cands.append((z_vwap, sym, px, vwap, sd))

        cands.sort(key=lambda x: (x[0], x[1]))
        for z_v, sym, px, vwap, sd in cands[: max_pos - open_after]:
            stop_pct = max(s_min, min(s_max, (stop_mult * sd) / px))
            stop_px = round(px * (1.0 - stop_pct), 4)
            tp_px = round(vwap, 4)
            shares = math.floor(((equity * w) / px) * 10000.0) / 10000.0
            if shares <= 0 or tp_px <= px:
                continue
            signals.append(
                OrderSignal(
                    agent_id=self.agent_id,
                    action="BUY",
                    ticker=sym,
                    stop_loss=stop_px,
                    take_profit=tp_px,
                    confidence=0.72,
                    reasoning=f"ENTRY_VWAP_REV: z={z_v:.2f}σ below VWAP ${vwap:.2f}",
                    requested_shares=shares,
                    decision_timestamp=snapshot.decision_timestamp,
                )
            )
        return signals or [self.hold_signal(snapshot, reasoning="HOLD_VWAP: no -2σ VWAP setups")]


class SqueezeBreakoutAgent(BasePartBAgent):
    """#35 — Daily Bollinger Band squeeze (bottom 20% of 60d width) -> intraday 30m upper BB breakout."""

    DEFAULT_AGENT_ID = "squeeze_breakout"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        tdate = snapshot.trading_date_et
        dates: List[str] = list(mem.get("dates_seen") or [])
        if tdate and tdate not in dates:
            dates.append(tdate)
            dates.sort()
        mem["dates_seen"] = dates
        day_idx = dates.index(tdate) if tdate in dates else 0

        entry_day_map: Dict[str, int] = dict(mem.get("entry_day_map") or {})
        max_hold = int(self.cfg.get("max_hold_days", 5))
        signals: List[OrderSignal] = []
        exiting: set[str] = set()

        # 1. 5-day time exit on existing positions
        for sym, pos in sorted(state.positions.items()):
            if pos.shares <= 0:
                continue
            ed = entry_day_map.get(sym)
            if ed is not None and (day_idx - int(ed)) >= max_hold:
                exiting.add(sym)
                entry_day_map.pop(sym, None)
                signals.append(
                    OrderSignal(
                        agent_id=self.agent_id,
                        action="SELL",
                        ticker=sym,
                        stop_loss=None,
                        take_profit=None,
                        confidence=0.80,
                        reasoning=f"EXIT_TIME_5D: squeeze breakout position reached {max_hold}-day hold limit",
                        requested_shares=pos.shares,
                        decision_timestamp=snapshot.decision_timestamp,
                    )
                )

        # Clean up stale entries from map
        for sym in list(entry_day_map.keys()):
            if sym not in state.positions or state.positions[sym].shares <= 0:
                entry_day_map.pop(sym, None)

        # 2. Cache daily squeezed watchlist once per day strictly from D-1 completed daily bars
        if mem.get("squeeze_cache_date") != tdate:
            bb_p = int(self.cfg.get("bb_period", 20))
            w_lb = int(self.cfg.get("width_lookback_days", 60))
            q_cut = float(self.cfg.get("squeeze_quantile", 0.20))
            squeezed: Dict[str, float] = {}
            for sym in sorted(LIQUID_LARGE_CAP_STOCKS):
                bars = strict_prior_daily_bars(snapshot, sym)
                closes = [float(b.close) for b in bars if b.close > 0]
                if len(closes) < bb_p + w_lb:
                    continue
                widths = []
                for k in range(len(closes) - w_lb, len(closes)):
                    w_val = compute_bb_width(closes[: k + 1], bb_p)
                    if w_val is not None:
                        widths.append(w_val)
                if len(widths) < 20:
                    continue
                cur_w = widths[-1]
                s_widths = sorted(widths)
                pct_rank = sum(1 for x in s_widths if x <= cur_w) / len(s_widths)
                if pct_rank <= q_cut:
                    atr_d = compute_wilder_atr(bars, 14) or (closes[-1] * 0.02)
                    squeezed[sym] = round(float(atr_d), 4)
            mem["squeeze_cache_date"] = tdate
            mem["squeezed_atr"] = squeezed
        else:
            squeezed = dict(mem.get("squeezed_atr") or {})

        # 3. Intraday breakout scan on squeezed tickers
        open_after = sum(1 for s, p in state.positions.items() if p.shares > 0 and s not in exiting)
        max_pos = int(self.cfg.get("max_positions", 4))
        min_vr = float(self.cfg.get("min_volume_ratio", 1.2))
        w = float(self.cfg.get("weight_per_ticker", 0.19))
        s_mult = float(self.cfg.get("stop_atr_mult", 1.5))
        s_min = float(self.cfg.get("stop_pct_min", 0.025))
        s_max = float(self.cfg.get("stop_pct_max", 0.050))
        tp_pct = float(self.cfg.get("tp_pct", 0.060))
        equity = float(state.equity) if state.equity > 0 else 10_000.0

        if open_after < max_pos and squeezed:
            cands = []
            for sym, atr_d in sorted(squeezed.items()):
                if sym in state.positions and state.positions[sym].shares > 0:
                    continue
                ind = (snapshot.indicators or {}).get(sym)
                if ind is None or ind.last_price <= 0 or ind.bb_upper_20 <= 0:
                    continue
                if ind.last_price > ind.bb_upper_20 and ind.volume_ratio >= min_vr:
                    breakout_pct = (ind.last_price / ind.bb_upper_20) - 1.0
                    cands.append((ind.volume_ratio, breakout_pct, sym, float(ind.last_price), float(atr_d)))

            cands.sort(key=lambda x: (-x[0], -x[1], x[2]))
            for vr, bp, sym, px, atr_d in cands[: max_pos - open_after]:
                stop_pct = max(s_min, min(s_max, (s_mult * atr_d) / px))
                stop_px = round(px * (1.0 - stop_pct), 4)
                tp_px = round(px * (1.0 + tp_pct), 4)
                shares = math.floor(((equity * w) / px) * 10000.0) / 10000.0
                if shares <= 0:
                    continue
                entry_day_map[sym] = day_idx
                signals.append(
                    OrderSignal(
                        agent_id=self.agent_id,
                        action="BUY",
                        ticker=sym,
                        stop_loss=stop_px,
                        take_profit=tp_px,
                        confidence=0.76,
                        reasoning=f"ENTRY_SQUEEZE_BO: BB squeeze breakout +{bp*100:.2f}%, vol_ratio={vr:.2f}x",
                        requested_shares=shares,
                        decision_timestamp=snapshot.decision_timestamp,
                    )
                )

        mem["entry_day_map"] = entry_day_map
        self.save_mem(mem)
        return signals or [self.hold_signal(snapshot, reasoning=f"HOLD_SQUEEZE: {len(squeezed)} squeezed stocks monitored")]
