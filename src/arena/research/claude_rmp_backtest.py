"""Section 11 Pre-Validation Protocol for Agent 14 (Claude Residual-Momentum Pullback).

Hard rules enforced:
- Uses ONLY the pre-fixed locked parameters in `claude_rmp_params.json`. Zero grid search or optimization.
- 2-year daily-bar approximation backtest:
  - Signals evaluated strictly on completed daily close `t`.
  - Orders fill at next trading day open `t+1` + half-spread + 5 bps slippage.
  - Stops checked against daily low; if next open < stop, fills at open (gap-down realism).
- Reports rolling 10-trading-day window return distribution, % of windows beating SPY,
  in-window max drawdown, BLOWN_UP (-10%) count (target: 0), average R per trade, average
  market exposure, plus explicit survivorship-bias disclosure.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arena.agents.claude_rmp_agent import (
    PARAMS_PATH,
    compute_daily_regime_and_leaders,
    compute_wilder_rsi,
    load_claude_rmp_params,
)
from arena.config import (
    DEFAULT_SPREAD_BPS,
    INITIAL_CASH,
    LIQUID_LARGE_CAP_STOCKS,
    SLIPPAGE_RATE,
    STATE_DIR,
)
from arena.models import Bar
from arena.research.market_history import UNIVERSE_FUNDAMENTALS_PATH, MarketHistoryCache

PREVALIDATION_OUTPUT_PATH = STATE_DIR / "claude_rmp_prevalidation.json"


def _quantile(vals: List[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    pos = (len(s) - 1) * max(0.0, min(1.0, q))
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return round(s[lo], 2)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 2)


def _build_daily_bar_objects(market: MarketHistoryCache, tickers: List[str]) -> Dict[str, List[Bar]]:
    out: Dict[str, List[Bar]] = {}
    for sym in tickers:
        hist = market.get_price_history(sym)
        if not hist or not hist.get("dates"):
            continue
        dates = hist["dates"]
        opens = hist["open"]
        highs = hist["high"]
        lows = hist["low"]
        closes = hist["close"]
        adj = hist["adjclose"]
        vols = hist["volume"]
        bars: List[Bar] = []
        for i, d_str in enumerate(dates):
            c = float(closes[i])
            a = float(adj[i]) if adj[i] else c
            ratio = (a / c) if c > 0 else 1.0
            bars.append(
                Bar(
                    ticker=sym,
                    timestamp=f"{d_str}T21:00:00Z",
                    open=round(float(opens[i]) * ratio, 4),
                    high=round(float(highs[i]) * ratio, 4),
                    low=round(float(lows[i]) * ratio, 4),
                    close=round(a, 4),
                    volume=float(vols[i]),
                )
            )
        out[sym] = bars
    return out


def _load_sector_map() -> Dict[str, str]:
    if UNIVERSE_FUNDAMENTALS_PATH.exists():
        try:
            data = json.loads(UNIVERSE_FUNDAMENTALS_PATH.read_text(encoding="utf-8"))
            return {k.upper(): str(v.get("sector", "Unknown")) for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            pass
    return {}


def _simulate_window(
    day_indices: List[int],
    spy_dates: List[str],
    bar_by_sym_date: Dict[str, Dict[str, Bar]],
    precomputed_daily: Dict[str, Dict[str, Any]],
    params: Dict[str, Any],
    sector_map: Dict[str, str],
    no_new_on_last_day: bool = True,
) -> Dict[str, Any]:
    """Simulate Agent 14 over `day_indices` (e.g. a 10-trading-day competition window or full 2y span).

    Daily-bar approximation per Section 11:
    - Pending orders decided on day `t` close fill on day `t+1` open + half-spread + 5 bps slippage.
    - Stops checked against day low; if day open < stop, fill at day open.
    """
    sr = params["sizing_and_risk"]
    ec = params["entry"]
    xc = params["exit"]
    half_sp_rate = DEFAULT_SPREAD_BPS / 20_000.0

    cash = INITIAL_CASH
    peak_equity = INITIAL_CASH
    max_dd_pct = 0.0
    blown_up = False
    positions: Dict[str, Dict[str, Any]] = {}
    cooldown_until: Dict[str, int] = {}
    pending_buys: List[Dict[str, Any]] = []
    pending_sells: List[Tuple[str, str]] = []
    closed_r_multiples: List[float] = []
    daily_exposures: List[float] = []
    spy_up_exposures: List[float] = []
    spy_down_exposures: List[float] = []
    trades_count = 0

    for step_idx, g_idx in enumerate(day_indices):
        d_str = spy_dates[g_idx]
        is_last_day = (step_idx == len(day_indices) - 1)
        prev_day_equity_start = cash + sum(
            pos["shares"] * (bar_by_sym_date.get(sym, {}).get(spy_dates[g_idx - 1]).close if bar_by_sym_date.get(sym, {}).get(spy_dates[g_idx - 1]) else pos["entry_price"])
            for sym, pos in positions.items()
        )

        # 1. Execute pending SELLs at today's open (decided at yesterday's close)
        for sym, reason in pending_sells:
            pos = positions.get(sym)
            if not pos:
                continue
            b_today = bar_by_sym_date.get(sym, {}).get(d_str)
            if not b_today:
                continue
            fill_px = (b_today.open * (1.0 - half_sp_rate)) * (1.0 - SLIPPAGE_RATE)
            cash += pos["shares"] * fill_px
            r_mult = (fill_px - pos["entry_price"]) / max(0.01, pos["initial_r"])
            closed_r_multiples.append(r_mult)
            trades_count += 1
            del positions[sym]
        pending_sells = []

        # 2. Execute pending BUYs at today's open (decided at yesterday's close)
        if not blown_up:
            for order in pending_buys:
                sym = order["ticker"]
                if sym in positions:
                    continue
                b_today = bar_by_sym_date.get(sym, {}).get(d_str)
                if not b_today or b_today.open <= 0:
                    continue
                fill_px = (b_today.open * (1.0 + half_sp_rate)) * (1.0 + SLIPPAGE_RATE)
                d_stop = order["stop_pct"]
                stop_px = round(fill_px * (1.0 - d_stop), 4)
                stop_dist = max(0.01, fill_px - stop_px)
                qty = order["target_r_dollars"] / stop_dist
                # Clip to 20% equity and available cash
                cur_eq_approx = cash + sum(p["shares"] * p["entry_price"] for p in positions.values())
                max_qty_20 = (cur_eq_approx * 0.20) / fill_px
                max_qty_cash = (cash * 0.995) / fill_px
                shares = math.floor(min(qty, max_qty_20, max_qty_cash) * 10_000) / 10_000
                notional = shares * fill_px
                if shares <= 0 or notional < 20.0:
                    continue
                cash -= notional
                positions[sym] = {
                    "shares": shares,
                    "entry_price": fill_px,
                    "initial_stop": stop_px,
                    "initial_r": stop_dist,
                    "current_stop": stop_px,
                    "highest_price": fill_px,
                    "entry_step": step_idx,
                }
                trades_count += 1
        pending_buys = []

        # 3. Intrabar stop-loss check on daily low (if open < stop, fill at open!)
        for sym in list(positions.keys()):
            pos = positions[sym]
            b_today = bar_by_sym_date.get(sym, {}).get(d_str)
            if not b_today:
                continue
            stop_px = pos["current_stop"]
            if b_today.open <= stop_px:
                raw_exit = b_today.open
                hit = True
            elif b_today.low <= stop_px:
                raw_exit = stop_px
                hit = True
            else:
                hit = False
            if hit:
                fill_px = (raw_exit * (1.0 - half_sp_rate)) * (1.0 - SLIPPAGE_RATE)
                cash += pos["shares"] * fill_px
                r_mult = (fill_px - pos["entry_price"]) / max(0.01, pos["initial_r"])
                closed_r_multiples.append(r_mult)
                trades_count += 1
                if stop_px <= pos["initial_stop"] + 1e-6:
                    cooldown_until[sym] = step_idx + int(sr["stopout_cooldown_trading_days"])
                del positions[sym]
            else:
                # Ratchet stop up using today's high (Section 8 #2)
                pos["highest_price"] = max(pos["highest_price"], b_today.high)
                sd = (precomputed_daily.get(d_str) or {}).get("daily_stats", {}).get(sym) or {}
                atr14 = float(sd.get("atr14") or (pos["entry_price"] * 0.02))
                new_stop = pos["current_stop"]
                if pos["highest_price"] >= pos["entry_price"] + float(xc["breakeven_trigger_r"]) * pos["initial_r"]:
                    new_stop = max(new_stop, pos["entry_price"] + float(xc["breakeven_lock_r"]) * pos["initial_r"])
                if pos["highest_price"] >= pos["entry_price"] + float(xc["trail_trigger_r"]) * pos["initial_r"]:
                    new_stop = max(new_stop, pos["highest_price"] - float(xc["trail_atr_mult"]) * atr14)
                pos["current_stop"] = round(new_stop, 4)

        # 4. Mark-to-market at close & exposure / drawdown tracking
        pos_val = 0.0
        for sym, pos in positions.items():
            b_today = bar_by_sym_date.get(sym, {}).get(d_str)
            px = b_today.close if b_today else pos["entry_price"]
            pos_val += pos["shares"] * px
        equity = cash + pos_val
        peak_equity = max(peak_equity, equity)
        dd_frac = (equity / peak_equity) - 1.0
        dd_pct = dd_frac * 100.0
        max_dd_pct = min(max_dd_pct, dd_pct)

        exp_pct = (pos_val / equity) * 100.0 if equity > 0 else 0.0
        daily_exposures.append(exp_pct)
        spy_today = bar_by_sym_date.get("SPY", {}).get(d_str)
        spy_prev = bar_by_sym_date.get("SPY", {}).get(spy_dates[g_idx - 1])
        spy_day_ret = ((spy_today.close / spy_prev.close) - 1.0) if (spy_today and spy_prev and spy_prev.close > 0) else 0.0
        if spy_day_ret >= 0:
            spy_up_exposures.append(exp_pct)
        else:
            spy_down_exposures.append(exp_pct)

        # Check -10% BLOWN_UP circuit breaker
        if dd_pct <= -10.0:
            blown_up = True
            for sym, pos in list(positions.items()):
                b_today = bar_by_sym_date.get(sym, {}).get(d_str)
                px = b_today.close if b_today else pos["entry_price"]
                fill_px = (px * (1.0 - half_sp_rate)) * (1.0 - SLIPPAGE_RATE)
                cash += pos["shares"] * fill_px
                r_mult = (fill_px - pos["entry_price"]) / max(0.01, pos["initial_r"])
                closed_r_multiples.append(r_mult)
                trades_count += 1
                del positions[sym]
            continue

        if is_last_day:
            continue

        # 5. Evaluate end-of-day exits (3: Time, 4: SMA50, 5: Regime OFF) -> queue for next-day open
        daily_info = precomputed_daily.get(d_str)
        if not daily_info or not daily_info.get("valid"):
            continue
        slots = int(daily_info["slots"])
        d_stats = daily_info["daily_stats"]

        exiting_set = set()
        for sym, pos in list(positions.items()):
            b_today = bar_by_sym_date.get(sym, {}).get(d_str)
            if not b_today:
                continue
            p_close = b_today.close
            unreal_r = (p_close - pos["entry_price"]) / max(0.01, pos["initial_r"])
            sd = d_stats.get(sym) or {}
            sma50 = sd.get("sma50")
            atr14 = float(sd.get("atr14") or (pos["entry_price"] * 0.02))

            if (step_idx - pos["entry_step"]) >= int(xc["time_stop_trading_days"]) and unreal_r < float(xc["time_stop_min_unrealized_r"]):
                pending_sells.append((sym, "EXIT_TIME"))
                exiting_set.add(sym)
            elif sma50 is not None and p_close < float(sma50):
                pending_sells.append((sym, "EXIT_SMA50"))
                exiting_set.add(sym)
            elif slots == 0:
                if unreal_r < float(xc["regime_off_min_unrealized_r"]):
                    pending_sells.append((sym, "EXIT_REGIME"))
                    exiting_set.add(sym)
                else:
                    pos["current_stop"] = round(max(pos["current_stop"], p_close - float(xc["regime_off_tighten_atr_mult"]) * atr14), 4)

        # 6. Evaluate end-of-day pullback entry triggers (E1..E5 daily approx) -> queue for next-day open
        # Note: if `no_new_on_last_day` and next day is the last day of a 10-day contest, skip entering on day 9 close
        next_is_last = (step_idx + 1 == len(day_indices) - 1)
        if no_new_on_last_day and next_is_last and len(day_indices) <= 12:
            continue

        daily_pnl_pct = ((equity / prev_day_equity_start) - 1.0) * 100.0 if prev_day_equity_start > 0 else 0.0
        if slots <= 0 or spy_day_ret <= (float(params["regime"]["intraday_spy_crash_guard_pct"]) / 100.0) or daily_pnl_pct <= float(sr["daily_loss_halt_pct"]):
            continue

        # Compute Heat & HeatCap
        heat = 0.0
        sec_counts: Dict[str, int] = {}
        active_cnt = 0
        for sym, pos in positions.items():
            if sym in exiting_set:
                continue
            active_cnt += 1
            b_today = bar_by_sym_date.get(sym, {}).get(d_str)
            p_c = b_today.close if b_today else pos["entry_price"]
            heat += max(0.0, (p_c - pos["current_stop"]) * pos["shares"])
            sec = sector_map.get(sym, "Unknown")
            sec_counts[sec] = sec_counts.get(sec, 0) + 1

        heat_cap_pct = min(float(sr["heat_cap_base_pct"]) / 100.0, (float(sr["heat_cap_dd_intercept_pct"]) / 100.0) + dd_frac)
        heat_cap = max(0.0, equity * heat_cap_pct)
        rem_slots = max(0, slots - active_cnt)
        added_today = 0

        for leader in daily_info["leaders_top10"]:
            if rem_slots <= 0 or added_today >= int(ec["max_new_per_cycle"]):
                break
            sym = leader["ticker"]
            if sym in positions or step_idx < cooldown_until.get(sym, -1):
                continue
            b_today = bar_by_sym_date.get(sym, {}).get(d_str)
            sd = d_stats.get(sym)
            if not b_today or not sd:
                continue

            p_c = b_today.close
            atr14 = float(sd["atr14"])
            sma50 = float(sd["sma50"])
            h10 = float(sd["h10"])
            prev_c = float(sd["prev_close"])

            pullback_atr = (h10 - p_c) / atr14 if atr14 > 0 else -99.0
            e1 = float(ec["pullback_atr_min"]) <= pullback_atr <= float(ec["pullback_atr_max"])
            e2 = p_c > sma50
            rsi2 = compute_wilder_rsi(list(sd["closes_tail"]) + [p_c], period=2)
            e3 = (rsi2 is not None) and (rsi2 <= float(ec["rsi2_max"]))
            # Daily approximation of E4 intraday bounce: close off daily low by >= 0.2 * ATR14
            e4 = p_c >= (b_today.low + float(ec["intraday_bounce_atr_mult"]) * atr14)
            s_ret = ((p_c / prev_c) - 1.0) if prev_c > 0 else 0.0
            e5 = (s_ret - spy_day_ret) >= (float(ec["min_relative_intraday_vs_spy_pct"]) / 100.0)
            if not (e1 and e2 and e3 and e4 and e5):
                continue

            sec = sector_map.get(sym, "Unknown")
            if sec != "Unknown" and sec_counts.get(sec, 0) >= int(ec["max_per_sector"]):
                continue

            r_dollars = min((float(sr["target_risk_per_trade_pct"]) / 100.0) * equity, max(0.0, heat_cap - heat))
            if r_dollars < (float(sr["min_risk_per_trade_pct"]) / 100.0) * equity:
                continue

            raw_d = (float(sr["stop_atr_mult"]) * atr14) / p_c
            d_stop = max(float(sr["stop_min_pct"]), min(float(sr["stop_max_pct"]), raw_d))
            pending_buys.append({"ticker": sym, "stop_pct": d_stop, "target_r_dollars": r_dollars})
            heat += r_dollars
            rem_slots -= 1
            added_today += 1
            if sec != "Unknown":
                sec_counts[sec] = sec_counts.get(sec, 0) + 1

    # Final mark-to-market at end of window
    last_d = spy_dates[day_indices[-1]]
    final_equity = cash
    for sym, pos in positions.items():
        b_last = bar_by_sym_date.get(sym, {}).get(last_d)
        px = b_last.close if b_last else pos["entry_price"]
        final_equity += pos["shares"] * px

    # SPY buy-and-hold over exact same window (enter Day 1 open + friction -> Day N close)
    spy_first = bar_by_sym_date.get("SPY", {}).get(spy_dates[day_indices[0]])
    spy_last = bar_by_sym_date.get("SPY", {}).get(last_d)
    if spy_first and spy_last and spy_first.open > 0:
        spy_in = (spy_first.open * (1.0 + half_sp_rate)) * (1.0 + SLIPPAGE_RATE)
        spy_ret_pct = round(((spy_last.close - spy_in) / spy_in) * 100.0, 2)
    else:
        spy_ret_pct = 0.0

    ret_pct = round(((final_equity - INITIAL_CASH) / INITIAL_CASH) * 100.0, 2)
    return {
        "start_date": spy_dates[day_indices[0]],
        "end_date": last_d,
        "return_pct": ret_pct,
        "spy_return_pct": spy_ret_pct,
        "excess_vs_spy_pct": round(ret_pct - spy_ret_pct, 2),
        "beat_spy": ret_pct > spy_ret_pct,
        "max_drawdown_pct": round(max_dd_pct, 2),
        "blown_up": blown_up,
        "trades_count": trades_count,
        "closed_r_multiples": closed_r_multiples,
        "avg_exposure_pct": round(sum(daily_exposures) / len(daily_exposures), 2) if daily_exposures else 0.0,
        "avg_exposure_spy_up_pct": round(sum(spy_up_exposures) / len(spy_up_exposures), 2) if spy_up_exposures else 0.0,
        "avg_exposure_spy_down_pct": round(sum(spy_down_exposures) / len(spy_down_exposures), 2) if spy_down_exposures else 0.0,
    }


def run_2year_prevalidation(
    as_of_end_date: str = "2026-11-20",
    years: int = 2,
    output_path: Path = PREVALIDATION_OUTPUT_PATH,
    offline: bool = False,
) -> Dict[str, Any]:
    """Execute Section 11 Pre-Validation Protocol on locked parameters over the last 2 years."""
    params = load_claude_rmp_params(PARAMS_PATH)
    market = MarketHistoryCache(offline=offline)
    sector_map = _load_sector_map()

    all_tickers = ["SPY"] + list(LIQUID_LARGE_CAP_STOCKS)
    bars_map = _build_daily_bar_objects(market, all_tickers)
    spy_bars = [b for b in bars_map.get("SPY", []) if b.timestamp[:10] <= as_of_end_date]
    spy_dates = [b.timestamp[:10] for b in spy_bars]

    end_dt = date.fromisoformat(spy_dates[-1])
    start_2y_str = (end_dt - timedelta(days=years * 365)).isoformat()

    # Build fast lookup {sym: {date_str: Bar}}
    bar_by_sym_date: Dict[str, Dict[str, Bar]] = {}
    for sym, blist in bars_map.items():
        bar_by_sym_date[sym] = {b.timestamp[:10]: b for b in blist}

    # Find indices covering the last 2 years (requiring >= 70 prior completed bars)
    test_indices = [i for i, d in enumerate(spy_dates) if d >= start_2y_str and i >= 72]
    if len(test_indices) < 20:
        raise RuntimeError(f"Insufficient 2-year SPY daily history ({len(test_indices)} days).")

    # Precompute daily Regime & Leader Top 10 strictly from bars strictly BEFORE each date `d`
    precomputed_daily: Dict[str, Dict[str, Any]] = {}
    for idx in test_indices:
        d_str = spy_dates[idx]
        # Strictly completed bars up to index idx - 1 (prior trading day close)
        prev_cut_date = spy_dates[idx - 1]
        daily_slice: Dict[str, List[Bar]] = {}
        for sym, blist in bars_map.items():
            # Slice bars with date <= prev_cut_date (last 80 bars is plenty for 63-day beta & 50-day SMA slope)
            sub = [b for b in blist if b.timestamp[:10] <= prev_cut_date]
            if sub:
                daily_slice[sym] = sub[-90:]
        precomputed_daily[d_str] = compute_daily_regime_and_leaders(
            daily_bars_by_ticker=daily_slice,
            params=params,
            eligible_stocks=LIQUID_LARGE_CAP_STOCKS,
        )

    # 1. Full continuous 2-year daily approximation backtest
    full_2y = _simulate_window(
        day_indices=test_indices,
        spy_dates=spy_dates,
        bar_by_sym_date=bar_by_sym_date,
        precomputed_daily=precomputed_daily,
        params=params,
        sector_map=sector_map,
        no_new_on_last_day=False,
    )

    # 2. Rolling 10-trading-day windows across the entire 2-year span (step=1 trading day)
    rolling_returns: List[float] = []
    rolling_spy_returns: List[float] = []
    rolling_excess: List[float] = []
    rolling_dds: List[float] = []
    rolling_exposures: List[float] = []
    rolling_all_r: List[float] = []
    beat_spy_count = 0
    blown_up_count = 0

    num_windows = len(test_indices) - 9
    for w in range(num_windows):
        win_indices = test_indices[w : w + 10]
        res = _simulate_window(
            day_indices=win_indices,
            spy_dates=spy_dates,
            bar_by_sym_date=bar_by_sym_date,
            precomputed_daily=precomputed_daily,
            params=params,
            sector_map=sector_map,
            no_new_on_last_day=True,
        )
        rolling_returns.append(res["return_pct"])
        rolling_spy_returns.append(res["spy_return_pct"])
        rolling_excess.append(res["excess_vs_spy_pct"])
        rolling_dds.append(res["max_drawdown_pct"])
        rolling_exposures.append(res["avg_exposure_pct"])
        rolling_all_r.extend(res["closed_r_multiples"])
        if res["beat_spy"]:
            beat_spy_count += 1
        if res["blown_up"]:
            blown_up_count += 1

    beat_spy_ratio_pct = round((beat_spy_count / max(1, num_windows)) * 100.0, 1)
    avg_r_2y = round(sum(full_2y["closed_r_multiples"]) / len(full_2y["closed_r_multiples"]), 3) if full_2y["closed_r_multiples"] else 0.0
    avg_r_rolling = round(sum(rolling_all_r) / len(rolling_all_r), 3) if rolling_all_r else 0.0
    avg_exp_rolling = round(sum(rolling_exposures) / len(rolling_exposures), 2) if rolling_exposures else 0.0

    result = {
        "agent_id": "claude_rmp",
        "agent_name": "14. Claude Residual-Momentum Pullback",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parameters_locked_no_optimization": True,
        "backtest_period": [spy_dates[test_indices[0]], spy_dates[test_indices[-1]]],
        "total_trading_days": len(test_indices),
        "survivorship_bias_warning": (
            "CAUTION: This 2-year historical pre-validation uses today's 64 liquid large-cap universe, "
            "which introduces survivorship bias and makes historical results more optimistic than live trading."
        ),
        "rolling_10d_summary": {
            "windows_count": num_windows,
            "return_distribution_pct": {
                "min": _quantile(rolling_returns, 0.0),
                "p10": _quantile(rolling_returns, 0.10),
                "p25": _quantile(rolling_returns, 0.25),
                "median": _quantile(rolling_returns, 0.50),
                "mean": round(sum(rolling_returns) / max(1, len(rolling_returns)), 2),
                "p75": _quantile(rolling_returns, 0.75),
                "p90": _quantile(rolling_returns, 0.90),
                "max": _quantile(rolling_returns, 1.0),
            },
            "spy_10d_median_pct": _quantile(rolling_spy_returns, 0.50),
            "spy_10d_mean_pct": round(sum(rolling_spy_returns) / max(1, len(rolling_spy_returns)), 2),
            "beat_spy_windows_count": beat_spy_count,
            "beat_spy_windows_pct": beat_spy_ratio_pct,
            "worst_window_max_drawdown_pct": _quantile(rolling_dds, 0.0),
            "median_window_max_drawdown_pct": _quantile(rolling_dds, 0.50),
            "blown_up_count": blown_up_count,
            "blown_up_target": 0,
            "avg_r_per_closed_trade": avg_r_rolling,
            "avg_exposure_pct": avg_exp_rolling,
        },
        "continuous_2y_summary": {
            "return_pct": full_2y["return_pct"],
            "spy_return_pct": full_2y["spy_return_pct"],
            "excess_vs_spy_pct": full_2y["excess_vs_spy_pct"],
            "beat_spy": full_2y["beat_spy"],
            "max_drawdown_pct": full_2y["max_drawdown_pct"],
            "blown_up": full_2y["blown_up"],
            "trades_count": full_2y["trades_count"],
            "closed_trades_count": len(full_2y["closed_r_multiples"]),
            "avg_r_per_closed_trade": avg_r_2y,
            "avg_exposure_pct": full_2y["avg_exposure_pct"],
            "avg_exposure_spy_up_pct": full_2y["avg_exposure_spy_up_pct"],
            "avg_exposure_spy_down_pct": full_2y["avg_exposure_spy_down_pct"],
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    res = run_2year_prevalidation()
    print(json.dumps(res, indent=2))
