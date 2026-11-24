"""Part B-D: Market Timing & Sentiment Agents (#31–#33).

- #31 breadth_timing      : 53-stock % above SMA20 -> 0% / ~50% / ~95% broad market ETF equity exposure
- #32 panic_dip_index     : Buys SPY/QQQ after >= 3% 3-day SPY drop (staged add-on on extra -1.5%), 5-day hold
- #33 risk_appetite_ratio : XLY/XLP + SMH/SPY + IWM/SPY vs 20d SMA score -> QQQ+growth / XLP+XLU+defensive / Cash
"""

from __future__ import annotations

from typing import Dict, List

from ..config import LIQUID_LARGE_CAP_STOCKS
from ..models import AgentLedgerState, MarketSnapshot, OrderSignal
from .claude_rmp_agent import compute_sma
from .part_b_common import (
    BasePartBAgent,
    get_last_price,
    strict_prior_daily_bars,
)


class BreadthTimingAgent(BasePartBAgent):
    """#31 — Adjusts broad equity exposure (0% / ~50% / ~95%) based on % of 53 stocks above SMA20."""

    DEFAULT_AGENT_ID = "breadth_timing"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        tdate = snapshot.trading_date_et
        if mem.get("last_checked_date") == tdate:
            return [self.hold_signal(snapshot, reasoning="HOLD_BREADTH: already evaluated today's breadth")]

        sma_p = int(self.cfg.get("sma_days", 20))
        above = 0
        total = 0
        for sym in LIQUID_LARGE_CAP_STOCKS:
            bars = strict_prior_daily_bars(snapshot, sym)
            closes = [float(b.close) for b in bars if b.close > 0]
            sma = compute_sma(closes, sma_p)
            if sma is not None and sma > 0:
                total += 1
                if closes[-1] > sma:
                    above += 1

        if total == 0:
            return [self.hold_signal(snapshot, reasoning="HOLD_BREADTH: insufficient daily history")]

        breadth = above / total
        hi_th = float(self.cfg.get("high_breadth_pct", 0.60))
        lo_th = float(self.cfg.get("low_breadth_pct", 0.40))

        if breadth >= hi_th:
            w = float(self.cfg.get("full_weight_each", 0.19))
            targets = {sym: w for sym in self.cfg.get("full_proxies", ["SPY", "QQQ", "DIA", "IWM", "XLK"])}
            band = "FULL"
        elif breadth >= lo_th:
            w = float(self.cfg.get("half_weight_each", 0.165))
            targets = {sym: w for sym in self.cfg.get("half_proxies", ["SPY", "DIA", "XLP"])}
            band = "HALF"
        else:
            targets = {}
            band = "CASH"

        mem["last_checked_date"] = tdate
        mem["last_breadth"] = round(breadth, 4)
        mem["last_band"] = band
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.045)),
            reason_prefix=f"BREADTH_{band}",
        )


class PanicDipIndexAgent(BasePartBAgent):
    """#32 — Buys SPY & QQQ when 3-day cumulative SPY drop <= -3.0%, holds up to 5 trading days."""

    DEFAULT_AGENT_ID = "panic_dip_index"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        tdate = snapshot.trading_date_et
        dates: List[str] = list(mem.get("dates_seen") or [])
        if tdate and tdate not in dates:
            dates.append(tdate)
            dates.sort()
        mem["dates_seen"] = dates
        day_idx = dates.index(tdate) if tdate in dates else 0

        # Check 5-trading-day time exit on existing positions
        max_hold = int(self.cfg.get("max_hold_days", 5))
        entry_idx = mem.get("entry_day_idx")
        if state.positions and entry_idx is not None and (day_idx - int(entry_idx)) >= max_hold:
            mem["entry_day_idx"] = None
            mem["trigger_spy_px"] = None
            self.save_mem(mem)
            return self.build_rebalance_signals(
                snapshot,
                state,
                target_weights={},
                stop_pct=float(self.cfg.get("stop_pct", 0.045)),
                reason_prefix="PANIC_HOLD_5D",
            )

        spy_bars = strict_prior_daily_bars(snapshot, "SPY")
        spy_closes = [float(b.close) for b in spy_bars if b.close > 0]
        if len(spy_closes) < 4:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_PANIC_DIP: insufficient SPY history")]

        spy_now = get_last_price(snapshot, "SPY", fallback=spy_closes[-1])
        # 3-day cumulative return using live price vs close 3 trading days prior (and completed D-1 3d return)
        ret_3d_live = (spy_now / spy_closes[-3]) - 1.0 if spy_closes[-3] > 0 else 0.0
        ret_3d_prior = (spy_closes[-1] / spy_closes[-4]) - 1.0 if spy_closes[-4] > 0 else 0.0
        worst_3d = min(ret_3d_live, ret_3d_prior)

        trig = float(self.cfg.get("spy_3d_drop_trigger", -0.030))
        w = float(self.cfg.get("weight_per_ticker", 0.195))
        tickers = list(self.cfg.get("tickers", ["SPY", "QQQ"]))

        if not state.positions and worst_3d <= trig:
            mem["entry_day_idx"] = day_idx
            mem["trigger_spy_px"] = spy_now
            self.save_mem(mem)
            # First tranche (half of cap) on initial -3% panic trigger
            targets = {sym: round(w * 0.55, 4) for sym in tickers}
            return self.build_rebalance_signals(
                snapshot,
                state,
                target_weights=targets,
                stop_pct=float(self.cfg.get("stop_pct", 0.045)),
                take_profit_pct=float(self.cfg.get("tp_pct", 0.050)),
                reason_prefix="PANIC_DIP_T1",
            )

        # Second tranche if SPY drops an additional -1.5% below initial trigger price
        if state.positions and mem.get("trigger_spy_px"):
            extra_drop = (spy_now / float(mem["trigger_spy_px"])) - 1.0
            if extra_drop <= float(self.cfg.get("add_on_extra_drop", -0.015)):
                mem["trigger_spy_px"] = None  # add-on once
                self.save_mem(mem)
                targets = {sym: w for sym in tickers}
                return self.build_rebalance_signals(
                    snapshot,
                    state,
                    target_weights=targets,
                    stop_pct=float(self.cfg.get("stop_pct", 0.045)),
                    take_profit_pct=float(self.cfg.get("tp_pct", 0.050)),
                    reason_prefix="PANIC_DIP_T2",
                )

        self.save_mem(mem)
        return [self.hold_signal(snapshot, reasoning=f"HOLD_PANIC_DIP: 3d SPY move {worst_3d*100:.2f}% (trigger <= {trig*100:.1f}%)")]


class RiskAppetiteRatioAgent(BasePartBAgent):
    """#33 — Scores XLY/XLP, SMH/SPY, IWM/SPY vs 20d SMA -> Growth ETFs / Defensive ETFs / Cash."""

    DEFAULT_AGENT_ID = "risk_appetite_ratio"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        tdate = snapshot.trading_date_et
        if mem.get("last_checked_date") == tdate:
            return [self.hold_signal(snapshot, reasoning="HOLD_RISK_APPETITE: already evaluated today")]

        sma_p = int(self.cfg.get("sma_days", 20))
        pairs = self.cfg.get("ratio_pairs", [["XLY", "XLP"], ["SMH", "SPY"], ["IWM", "SPY"]])
        score = 0
        valid_pairs = 0
        for num_sym, den_sym in pairs:
            b_num = strict_prior_daily_bars(snapshot, num_sym)
            b_den = strict_prior_daily_bars(snapshot, den_sym)
            m_num = {b.timestamp[:10]: float(b.close) for b in b_num if b.close > 0}
            m_den = {b.timestamp[:10]: float(b.close) for b in b_den if b.close > 0}
            common = sorted(set(m_num.keys()) & set(m_den.keys()))
            if len(common) < sma_p:
                continue
            ratios = [m_num[d] / m_den[d] for d in common[-sma_p:]]
            sma_r = sum(ratios) / len(ratios)
            valid_pairs += 1
            if ratios[-1] > sma_r:
                score += 1

        if valid_pairs == 0:
            return [self.hold_signal(snapshot, reasoning="HOLD_RISK_APPETITE: insufficient ETF ratio history")]

        if score >= 2:
            w = float(self.cfg.get("risk_on_weight_each", 0.19))
            targets = {sym: w for sym in self.cfg.get("risk_on_etfs", ["QQQ", "SMH", "XLY", "XLK"])}
            mode = f"RISK_ON_{score}OF{valid_pairs}"
        elif score == 1:
            w = float(self.cfg.get("defensive_weight_each", 0.15))
            targets = {sym: w for sym in self.cfg.get("defensive_etfs", ["XLP", "XLU", "GLD", "TLT"])}
            mode = f"DEFENSIVE_{score}OF{valid_pairs}"
        else:
            targets = {}
            mode = f"CASH_0OF{valid_pairs}"

        mem["last_checked_date"] = tdate
        mem["last_mode"] = mode
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.045)),
            reason_prefix=f"APPETITE_{mode}",
        )
