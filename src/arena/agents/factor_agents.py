"""Part B-C: Cross-Sectional Factor Agents over 53 Large-Cap Stocks (#26–#30).

- #26 xs_short_reversal   : Bottom 5 5-day return stocks (max 1/sector, non-news), daily rebalance
- #27 high52_anchor       : George & Hwang (2004) 70-day high proximity + positive 63d RS vs SPY (weekly)
- #28 value_momentum      : Bottom 50% P/E ratio intersected with Top 5 63-day return (weekly)
- #29 volume_accumulation : Top 5 20-day up-day/down-day volume ratio above SMA20 (weekly)
- #30 regime_beta_switch  : RISK_ON -> Top 5 high 63d beta; RISK_OFF -> Bottom 5 low 63d beta
"""

from __future__ import annotations

import statistics
from typing import Dict, List

from ..config import LIQUID_LARGE_CAP_STOCKS
from ..models import AgentLedgerState, MarketSnapshot, OrderSignal
from .allocation_agents import _should_weekly_rebalance
from .claude_rmp_agent import compute_realized_vol_annualized, compute_sma
from .part_b_common import (
    BasePartBAgent,
    compute_beta_n,
    compute_return_n,
    has_recent_news_veto,
    load_fundamentals_map,
    parse_hhmm_minutes,
    snapshot_et_minutes,
    strict_prior_daily_bars,
)


class CrossSectionalShortReversalAgent(BasePartBAgent):
    """#26 — Daily cross-sectional 5-day loser reversal (bottom 5 stocks, max 1 per GICS sector)."""

    DEFAULT_AGENT_ID = "xs_short_reversal"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        tdate = snapshot.trading_date_et
        mins = snapshot_et_minutes(snapshot)
        win = self.cfg.get("rebalance_et_window", ["10:30", "11:30"])
        w_start = parse_hhmm_minutes(str(win[0]), 10 * 60 + 30)
        w_end = parse_hhmm_minutes(str(win[1]), 11 * 60 + 30)

        if mem.get("last_rebalance_date") == tdate or not (w_start <= mins <= w_end):
            return [self.hold_signal(snapshot, reasoning="HOLD_XS_REVERSAL: outside daily rebalance window")]

        lb = int(self.cfg.get("lookback_days", 5))
        top_n = int(self.cfg.get("top_n", 5))
        max_sec = int(self.cfg.get("max_per_sector", 1))
        w = float(self.cfg.get("weight_per_ticker", 0.19))
        fmap = load_fundamentals_map()

        scored = []
        for sym in sorted(LIQUID_LARGE_CAP_STOCKS):
            bars = strict_prior_daily_bars(snapshot, sym)
            closes = [float(b.close) for b in bars if b.close > 0]
            ret5 = compute_return_n(closes, lb)
            if ret5 is None:
                continue
            if has_recent_news_veto(snapshot, sym, self.common_veto_keywords):
                continue
            sec = str((fmap.get(sym) or {}).get("sector", "Unknown"))
            scored.append((ret5, sym, sec))

        scored.sort(key=lambda x: (x[0], x[1]))  # lowest 5-day return first
        sec_cnt: Dict[str, int] = {}
        targets: Dict[str, float] = {}
        for _r5, sym, sec in scored:
            if len(targets) >= top_n:
                break
            if sec_cnt.get(sec, 0) >= max_sec:
                continue
            targets[sym] = w
            sec_cnt[sec] = sec_cnt.get(sec, 0) + 1

        mem["last_rebalance_date"] = tdate
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.05)),
            reason_prefix="XS_REVERSAL",
        )


class High52AnchorAgent(BasePartBAgent):
    """#27 — George & Hwang (2004) anchoring bias: closest to dynamic 70-day high with positive 63d RS vs SPY."""

    DEFAULT_AGENT_ID = "high52_anchor"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        interval = int(self.cfg.get("rebalance_interval_days", 5))
        due, day_idx = _should_weekly_rebalance(mem, snapshot, interval)
        if not due:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_HIGH_ANCHOR: between weekly rebalance dates")]

        hi_lb = int(self.cfg.get("high_lookback_days", 70))
        rs_lb = int(self.cfg.get("rs_lookback_days", 63))
        top_n = int(self.cfg.get("top_n", 5))
        w = float(self.cfg.get("weight_per_ticker", 0.19))

        spy_bars = strict_prior_daily_bars(snapshot, "SPY")
        spy_closes = [float(b.close) for b in spy_bars if b.close > 0]
        spy_ret63 = compute_return_n(spy_closes, rs_lb)
        if spy_ret63 is None:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_HIGH_ANCHOR: insufficient SPY 63d history")]

        scored = []
        for sym in sorted(LIQUID_LARGE_CAP_STOCKS):
            bars = strict_prior_daily_bars(snapshot, sym)
            closes = [float(b.close) for b in bars if b.close > 0]
            if len(closes) < max(hi_lb, rs_lb + 1):
                continue
            ret63 = compute_return_n(closes, rs_lb)
            if ret63 is None or (ret63 - spy_ret63) <= 0.0:
                continue
            high_n = max(closes[-hi_lb:])
            if high_n <= 0:
                continue
            ratio_to_high = closes[-1] / high_n
            scored.append((ratio_to_high, ret63 - spy_ret63, sym))

        scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
        targets = {sym: w for _ratio, _rs, sym in scored[:top_n]}

        mem["last_rebalance_date"] = snapshot.trading_date_et
        mem["last_rebalance_idx"] = day_idx
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.05)),
            reason_prefix="HIGH70_ANCHOR",
        )


class ValueMomentumAgent(BasePartBAgent):
    """#28 — Cheap half by P/E ratio intersected with top 5 63-day momentum stocks (weekly)."""

    DEFAULT_AGENT_ID = "value_momentum"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        interval = int(self.cfg.get("rebalance_interval_days", 5))
        due, day_idx = _should_weekly_rebalance(mem, snapshot, interval)
        if not due:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_VAL_MOM: between weekly rebalance dates")]

        mom_lb = int(self.cfg.get("mom_lookback_days", 63))
        top_n = int(self.cfg.get("top_n", 5))
        w = float(self.cfg.get("weight_per_ticker", 0.19))
        fmap = load_fundamentals_map()

        pe_rows = []
        for sym in sorted(LIQUID_LARGE_CAP_STOCKS):
            pe = (fmap.get(sym) or {}).get("pe_ratio")
            if pe is not None and 0 < float(pe) < 200.0:
                pe_rows.append((float(pe), sym))

        if len(pe_rows) < 6:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_VAL_MOM: insufficient P/E metadata")]

        median_pe = statistics.median(x[0] for x in pe_rows)
        cheap_syms = [sym for pe, sym in pe_rows if pe <= median_pe]

        scored = []
        for sym in cheap_syms:
            bars = strict_prior_daily_bars(snapshot, sym)
            closes = [float(b.close) for b in bars if b.close > 0]
            ret63 = compute_return_n(closes, mom_lb)
            if ret63 is not None and ret63 > 0.0:
                scored.append((ret63, sym))

        scored.sort(key=lambda x: (-x[0], x[1]))
        targets = {sym: w for _ret, sym in scored[:top_n]}

        mem["last_rebalance_date"] = snapshot.trading_date_et
        mem["last_rebalance_idx"] = day_idx
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.05)),
            reason_prefix="VAL_MOM",
        )


class VolumeAccumulationAgent(BasePartBAgent):
    """#29 — Top 5 stocks by 20-day Up-Day / Down-Day institutional volume accumulation ratio above SMA20."""

    DEFAULT_AGENT_ID = "volume_accumulation"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        interval = int(self.cfg.get("rebalance_interval_days", 5))
        due, day_idx = _should_weekly_rebalance(mem, snapshot, interval)
        if not due:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_VOL_ACCUM: between weekly rebalance dates")]

        lb = int(self.cfg.get("lookback_days", 20))
        req_sma20 = bool(self.cfg.get("require_above_sma20", True))
        top_n = int(self.cfg.get("top_n", 5))
        w = float(self.cfg.get("weight_per_ticker", 0.19))

        scored = []
        for sym in sorted(LIQUID_LARGE_CAP_STOCKS):
            bars = strict_prior_daily_bars(snapshot, sym)
            if len(bars) < lb + 1:
                continue
            closes = [float(b.close) for b in bars if b.close > 0]
            sma20 = compute_sma(closes, 20)
            if req_sma20 and (sma20 is None or closes[-1] <= sma20):
                continue
            up_vol = 0.0
            dn_vol = 0.0
            for i in range(len(bars) - lb, len(bars)):
                c_now, c_prev = float(bars[i].close), float(bars[i - 1].close)
                v = max(0.0, float(bars[i].volume))
                if c_now > c_prev:
                    up_vol += v
                elif c_now < c_prev:
                    dn_vol += v
            ratio = up_vol / max(dn_vol, 1.0)
            scored.append((ratio, sym))

        scored.sort(key=lambda x: (-x[0], x[1]))
        targets = {sym: w for _ratio, sym in scored[:top_n]}

        mem["last_rebalance_date"] = snapshot.trading_date_et
        mem["last_rebalance_idx"] = day_idx
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.05)),
            reason_prefix="VOL_ACCUM",
        )


class RegimeBetaSwitchAgent(BasePartBAgent):
    """#30 — Rotates between Top 5 high-beta stocks in RISK_ON and Bottom 5 low-beta stocks in RISK_OFF."""

    DEFAULT_AGENT_ID = "regime_beta_switch"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        tdate = snapshot.trading_date_et
        spy_bars = strict_prior_daily_bars(snapshot, "SPY")
        spy_closes = [float(b.close) for b in spy_bars if b.close > 0]
        beta_lb = int(self.cfg.get("beta_lookback_days", 63))
        if len(spy_closes) < beta_lb + 1:
            return [self.hold_signal(snapshot, reasoning="HOLD_BETA_SWITCH: insufficient SPY daily history")]

        spy_sma = compute_sma(spy_closes, int(self.cfg.get("spy_sma_days", 20))) or spy_closes[-1]
        spy_rv = compute_realized_vol_annualized(spy_closes, int(self.cfg.get("spy_rv_days", 20))) or 0.0
        regime = "RISK_ON" if (spy_closes[-1] > spy_sma and spy_rv < float(self.cfg.get("risk_on_rv_max", 0.25))) else "RISK_OFF"

        prev_regime = mem.get("last_regime")
        interval = int(self.cfg.get("rebalance_interval_days", 5))
        weekly_due, day_idx = _should_weekly_rebalance(mem, snapshot, interval)

        if not weekly_due and prev_regime == regime:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning=f"HOLD_BETA_SWITCH: regime unchanged ({regime})")]

        top_n = int(self.cfg.get("top_n", 5))
        w = float(self.cfg.get("weight_per_ticker", 0.19))

        betas = []
        for sym in sorted(LIQUID_LARGE_CAP_STOCKS):
            bars = strict_prior_daily_bars(snapshot, sym)
            closes = [float(b.close) for b in bars if b.close > 0]
            b_val = compute_beta_n(closes, spy_closes, beta_lb)
            if b_val is not None and b_val > 0.0:
                betas.append((b_val, sym))

        if regime == "RISK_ON":
            betas.sort(key=lambda x: (-x[0], x[1]))
        else:
            betas.sort(key=lambda x: (x[0], x[1]))
        targets = {sym: w for _b, sym in betas[:top_n]}

        mem["last_regime"] = regime
        mem["last_rebalance_date"] = tdate
        mem["last_rebalance_idx"] = day_idx
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.05)),
            reason_prefix=f"BETA_{regime}",
        )
