"""Daily Performance & Technical Regime Markdown Report Pipeline for AI Trading Arena.

Implements the 3-stage anti-hallucination reporting architecture:
1. 100% Python pre-calculation of all leaderboard, cohort, friction, open-position stop/target
   distances, circuit-breaker buffers, Agent 14 telemetry, and Day-10 Student-t statistics.
2. Smart-filtered lightweight market snapshot JSON (4 macro ETFs, breadth/RV/momentum aggregates,
   traded/held tickers only, top 15 Alpaca headlines).
3. Gemini Flash generation (temperature 0.15) with strict post-check validation and a 100%
   deterministic GFM Markdown renderer fallback so every trading day produces a verified report.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import statistics
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arena.config import (
    AGENT_SPECS,
    AGENT_SPECS_BY_ID,
    COMPETITION_TRADING_DAYS,
    INITIAL_CASH,
    LIQUID_LARGE_CAP_STOCKS,
    PROJECT_ROOT,
    STATE_DIR,
)
from arena.data.alpaca_client import (
    get_session_hours_et,
    is_us_trading_day,
    to_et_datetime,
)
from arena.engine.ledger import compute_leaderboard_metrics
from arena.models import AgentLedgerState, MarketSnapshot

logger = logging.getLogger(__name__)

REPORTS_DIR = PROJECT_ROOT / "reports"
DAILY_REPORTS_DIR = REPORTS_DIR / "daily"
LATEST_REPORT_PATH = REPORTS_DIR / "LATEST_REPORT.md"
MARKET_SUMMARY_CACHE_PATH = STATE_DIR / "last_market_summary.json"
REPORTS_LOG_PATH = STATE_DIR / "daily_reports_log.json"

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
PRIMARY_REPORT_MODEL = "gemini-2.5-flash"
FALLBACK_REPORT_MODEL = "gemini-2.0-flash"

# Two-tailed 95% Student-t critical values by degrees of freedom (df = n - 1)
_T_CRIT_95 = {
    1: 12.7062,
    2: 4.3027,
    3: 3.1824,
    4: 2.7764,
    5: 2.5706,
    6: 2.4469,
    7: 2.3646,
    8: 2.3060,
    9: 2.2622,
    10: 2.2281,
    11: 2.2010,
    12: 2.1788,
    15: 2.1314,
    20: 2.0860,
}

# Canonical strategy group labels matching the user's reporting specification
def classify_agent_group(agent_id: str, category: str) -> str:
    if agent_id in ("ctrl_spy", "ctrl_random"):
        return "SPY / 랜덤 (2)"
    cat_l = (category or "").lower()
    if "coded" in cat_l:
        return "코딩 기반 (5)"
    if "llm" in cat_l or "gemini" in cat_l:
        return "Gemini Flash LLM (4)"
    if "filing" in cat_l or "style" in cat_l:
        return "공시 스타일 학습 (2)"
    return "SPY / 랜덤 (2)"


DAILY_REPORT_SYSTEM_PROMPT = """# ROLE
당신은 기관급 퀀트 리서치 팀의 **수석 성과 분석가(Performance Analyst) 겸 시장 구조 전략가**입니다.
미국 주식·ETF 가상 모의투자 대회인 **AI Trading Arena(13개 에이전트: 1~12번, 14번 `claude_rmp`)**의 일일 JSON 데이터를 입력받아, GitHub 저장소(`.md`) 및 GitHub Pages 대시보드에 바로 게시할 수 있는 **객관적이고 정밀한 일일 성과·차트 분석 보고서**를 작성합니다.

# COMPETITION RULES & CONTEXT (필수 인지)
1. **거래 유니버스**: 미국 상장 유동성 대형주 46개 + 섹터/지수/매크로 ETF 18개 = 총 64개 종목.
   - 100% 가상 모의투자(Paper Trading), **Long 또는 현금(Cash) 전용** (공매도·레버리지·옵션·동전주 금지).
   - 예외 유니버스: **11·12·14번**은 개별 대형주만 매매, **4번**은 11개 SPDR 섹터 ETF만 매매.
2. **실행 주기 및 타임프레임**:
   - 메인 실행: 미국 정규장(09:30~16:00 ET) **30분봉(`30Min`)** 기준 매 30분 1사이클(일 13사이클, 총 10거래일, 휴장·조기마감 자동 반영).
   - 보조 타임프레임: 전일 완료 수정주가 **일봉(`1Day`, 최소 70봉~52주)** 활용 (14번 레짐·잔차 모멘텀·ATR/SMA50, 11·12번 52주 고점 대비 거리·3개월/30일 추세).
3. **리스크 및 탈락 규정**:
   - 초기 자금: 에이전트당 `$10,000`.
   - 거래 마찰: 실제 호가 스프레드(Bid/Ask) + `5 bps` 슬리피지 적용, 의사결정 후 다음 가격 체결.
   - 서킷 브레이커: 일일 손실률 `-3.0%` 도달 시 당일 신규 매매 정지(`DAILY_HALTED`), 누적 고점 대비 낙폭 `-10.0%` 도달 시 영구 탈락(`BLOWN_UP`).
4. **전략 수정 금지 원칙 (CRITICAL)**:
   - **대회 진행 중(1~10거래일)에는 어떠한 에이전트의 전략·파라미터·로직 수정도 금지됩니다.**
   - 보고서 내 "손절폭을 넓혀야 한다", "RSI 기준을 조정하라" 같은 **전략 수정/튜닝 제안을 절대 작성하지 마세요.** 대신 현재 설정 기준의 리스크 모니터링 및 다음 거래일 관전 포인트만 객관적으로 정리합니다.

# STRICT ANTI-HALLUCINATION & DATA INTEGRITY RULES (절대 준수)
1. **Zero Fabrication (추측·창작 금지)**: 모든 가격, 잔고, 수익률, 보조지표, 체결 내역, 거부 사유, 뉴스 헤드라인은 반드시 입력된 JSON 데이터에 근거해야 합니다.
2. **시각적 차트 환각 금지**: 입력은 100% 수치 JSON입니다. "헤드앤숄더", "아름다운 양봉" 같은 시각적 추측 표현을 금지하고 수치 기반 구조로만 서술하세요.
3. **결측치 및 0 나눗셈 처리**: 데이터가 없거나(`null`, 빈 배열) 산출 불가한 경우 정확히 `N/A` 또는 `-`로 표기하세요.
4. **사전 계산 수치 우선**: JSON에 Python으로 사전 계산된 숫자를 그대로 정확하게 표에 매핑하고 맥락을 해석하세요.

# OUTPUT FORMAT SPECIFICATION (GitHub Flavored Markdown)
- 표준 GitHub Flavored Markdown(GFM)만 사용(HTML 태그 금지).
- 건조하고 명확한 기관급 한국어 퀀트 리포트 문체(지표·티커·상태 코드는 영문 원어 유지).
- 반드시 아래 섹션 순서를 지키고, `is_final_day == false`일 때는 **8번 섹션(헤더 포함)을 완전히 생략**하세요:
  - `# AI Trading Arena 일일 리포트 — Day {TRADING_DAY}/10 ({DATE_ET})`
  - `## 1. 오늘의 핵심 요약 (Executive Summary)`
  - `## 2. 시장 매크로 및 기술적 레짐 브리핑` (`### 2.1`, `### 2.2`)
  - `## 3. 전체 에이전트 성과 리더보드 (13개 에이전트)`
  - `## 4. 전략군별 성과 비교`
  - `## 5. 스페셜 분석: 14번 claude_rmp 텔레메트리 및 가설 검증`
  - `## 6. 당일 매매 복기, 리스크 거부 및 마찰 감사` (`### 6.1`, `### 6.2`, `### 6.3`)
  - `## 7. 다음 거래일 관전 포인트 (모니터링 전용 · 전략 수정 금지)` (`### 7.1`, `### 7.2`)
  - (`is_final_day == true`일 때만) `## 8. 통계적 최종 평가: 운(Luck) vs 실력(Skill)`
"""


def _fmt_num(val: Optional[float], fmt: str = "{:.2f}", na: str = "N/A") -> str:
    if val is None or (isinstance(val, float) and not math.isfinite(val)):
        return na
    return fmt.format(val)


def _fmt_signed_pct(val: Optional[float], suffix: str = "%") -> str:
    if val is None or (isinstance(val, float) and not math.isfinite(val)):
        return "N/A"
    if abs(val) < 1e-9:
        val = 0.0
    return f"{val:+.2f}{suffix}"


def _fmt_money(val: Optional[float], signed: bool = False) -> str:
    if val is None or (isinstance(val, float) and not math.isfinite(val)):
        return "N/A"
    if signed:
        sign = "+" if val >= 0 else "-"
        return f"{sign}${abs(val):,.2f}"
    return f"${val:,.2f}"


def _get_next_trading_day_info(current_date_et: str) -> str:
    """Compute next US equity trading date and whether it is an early close or follows a holiday."""
    try:
        cur = date.fromisoformat(current_date_et)
    except Exception:
        return "다음 미국 거래일 일정 확인 필요"

    skipped_holidays: List[str] = []
    probe = cur + timedelta(days=1)
    while not is_us_trading_day(probe):
        if probe.weekday() < 5:
            skipped_holidays.append(probe.isoformat())
        probe += timedelta(days=1)

    open_et, close_et = get_session_hours_et(probe)
    sess_label = f"Early Close ({open_et}-{close_et} ET 조기마감)" if close_et != "16:00" else f"Regular Session ({open_et}-{close_et} ET)"
    if skipped_holidays:
        return f"{probe.isoformat()} {sess_label} (중간 미국 휴장일: {', '.join(skipped_holidays)})"
    return f"{probe.isoformat()} {sess_label}"


def _load_universe_fundamentals() -> Dict[str, Dict[str, Any]]:
    path = PROJECT_ROOT / "data_cache" / "universe_fundamentals.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Stage 2: Smart-Filtered Lightweight Market & Indicator Summary
# ---------------------------------------------------------------------------

def build_smart_market_summary(
    snapshot: MarketSnapshot,
    agent_states: List[AgentLedgerState],
    claude_rmp_logs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a compact, hallucination-proof market summary JSON from a MarketSnapshot.

    Filters 64 tickers down to:
    - 4 macro benchmarks (SPY, QQQ, TLT, GLD)
    - Market breadth, RV10/RV60, RS vs SPY Top/Bottom 5, Residual Momentum Top/Bottom 5,
      Volume Ratio Top 5, RSI/BB extremes, 52w-high proximity extremes
    - Active tickers (traded today or currently held by any agent)
    - Top 15 Alpaca news headlines
    """
    indicators = snapshot.indicators or {}
    fundamentals = _load_universe_fundamentals()
    latest_rmp = (claude_rmp_logs[-1] if claude_rmp_logs else {}) or {}

    # Compute SMA50 from daily_bars_by_ticker if available
    sma50_by_ticker: Dict[str, float] = {}
    for sym, dbars in (snapshot.daily_bars_by_ticker or {}).items():
        if len(dbars) >= 50:
            sma50_by_ticker[sym] = round(sum(float(b.close) for b in dbars[-50:]) / 50.0, 4)

    def _ticker_row(sym: str) -> Optional[Dict[str, Any]]:
        ind = indicators.get(sym)
        if not ind or ind.last_price <= 0:
            return None
        px = float(ind.last_price)
        sma50 = sma50_by_ticker.get(sym)
        orb_pos = "N/A"
        if ind.opening_range_high and ind.opening_range_low:
            if px > ind.opening_range_high:
                orb_pos = f"ORB High 돌파 (+{((px/ind.opening_range_high)-1)*100:.2f}%)"
            elif px < ind.opening_range_low:
                orb_pos = f"ORB Low 이탈 ({((px/ind.opening_range_low)-1)*100:.2f}%)"
            else:
                orb_pos = "ORB 범위 내"

        bb_pos = "밴드 내부"
        if ind.bb_upper_20 > 0 and px >= ind.bb_upper_20:
            bb_pos = "BB 상단 돌파"
        elif ind.bb_lower_20 > 0 and px <= ind.bb_lower_20:
            bb_pos = "BB 하단 이탈"

        fund = fundamentals.get(sym) or {}
        return {
            "ticker": sym,
            "close": round(px, 2),
            "change_pct_1d": round(float(ind.change_pct_1d), 2),
            "change_pct_5d": round(float(ind.change_pct_5d), 2),
            "orb_high": round(float(ind.opening_range_high), 2) if ind.opening_range_high else None,
            "orb_low": round(float(ind.opening_range_low), 2) if ind.opening_range_low else None,
            "orb_position": orb_pos,
            "sma_20": round(float(ind.sma_20), 2),
            "vs_sma20_pct": round(((px / ind.sma_20) - 1.0) * 100.0, 2) if ind.sma_20 > 0 else None,
            "sma_50": round(sma50, 2) if sma50 else None,
            "vs_sma50_pct": round(((px / sma50) - 1.0) * 100.0, 2) if sma50 and sma50 > 0 else None,
            "rsi_14": round(float(ind.rsi_14), 1),
            "atr_14": round(float(ind.atr_14), 2),
            "bb_position": bb_pos,
            "volume_ratio": round(float(ind.volume_ratio), 2),
            "rs_vs_spy": round(float(ind.relative_strength_vs_spy), 2),
            "dist_from_52w_high_pct": fund.get("dist_from_52w_high_pct"),
            "sector": fund.get("sector", "Unknown"),
        }

    # 1. Macro 4 benchmarks
    macro_assets = {}
    for m_sym in ("SPY", "QQQ", "TLT", "GLD"):
        row = _ticker_row(m_sym)
        if row:
            macro_assets[m_sym] = row

    # 2. Breadth across individual stocks & all 64 tickers
    stock_rows = [r for sym in LIQUID_LARGE_CAP_STOCKS if (r := _ticker_row(sym)) is not None]
    with_sma50 = [r for r in stock_rows if r["sma_50"] is not None]
    if with_sma50:
        above_sma50 = sum(1 for r in with_sma50 if r["close"] > r["sma_50"])
        breadth_above_sma50_pct = round((above_sma50 / len(with_sma50)) * 100.0, 1)
    elif latest_rmp.get("breadth_ratio") is not None:
        breadth_above_sma50_pct = round(float(latest_rmp["breadth_ratio"]) * 100.0, 1)
    else:
        breadth_above_sma50_pct = None

    # RV10/RV60 from SPY daily bars
    rv10_rv60_ratio = None
    spy_dbars = (snapshot.daily_bars_by_ticker or {}).get("SPY") or []
    if len(spy_dbars) >= 65:
        from arena.agents.claude_rmp_agent import compute_realized_vol_annualized

        spy_closes = [float(b.close) for b in spy_dbars]
        rv10 = compute_realized_vol_annualized(spy_closes, 10)
        rv60 = compute_realized_vol_annualized(spy_closes, 60)
        if rv10 is not None and rv60 and rv60 > 0:
            rv10_rv60_ratio = round(rv10 / rv60, 2)

    rs_sorted = sorted(stock_rows, key=lambda x: x["rs_vs_spy"], reverse=True)
    vol_sorted = sorted(stock_rows, key=lambda x: x["volume_ratio"], reverse=True)
    rsi_overbought = [
        {"ticker": r["ticker"], "rsi_14": r["rsi_14"], "bb": r["bb_position"]}
        for r in stock_rows if r["rsi_14"] >= 70.0
    ][:5]
    rsi_oversold = [
        {"ticker": r["ticker"], "rsi_14": r["rsi_14"], "bb": r["bb_position"]}
        for r in stock_rows if r["rsi_14"] <= 30.0
    ][:5]
    near_52w_high = [
        {"ticker": r["ticker"], "dist_52w_pct": r["dist_from_52w_high_pct"], "close": r["close"]}
        for r in sorted(
            [x for x in stock_rows if x.get("dist_from_52w_high_pct") is not None],
            key=lambda x: float(x["dist_from_52w_high_pct"]),
            reverse=True,
        )[:5]
    ]

    # 3. Traded today or currently held tickers
    active_tickers = set()
    tdate = snapshot.trading_date_et
    for st in agent_states:
        for pos_sym, pos in st.positions.items():
            if pos.shares > 0:
                active_tickers.add(pos_sym)
        for tr in st.trades:
            if _ts_to_et_date(tr.fill_timestamp) == tdate or _ts_to_et_date(tr.decision_timestamp) == tdate:
                active_tickers.add(tr.ticker)

    active_ticker_snapshots = {
        sym: row for sym in sorted(active_tickers) if (row := _ticker_row(sym)) is not None
    }

    # 4. Top 15 Alpaca headlines
    headlines = [
        {
            "created_at": n.created_at,
            "source": n.source,
            "symbols": n.symbols[:5],
            "headline": n.headline,
        }
        for n in (snapshot.news or [])[:15]
    ]

    return {
        "trading_date_et": tdate,
        "decision_timestamp_utc": snapshot.decision_timestamp,
        "session_open_et": snapshot.session_open_et,
        "session_close_et": snapshot.session_close_et,
        "regime_summary": snapshot.regime_summary,
        "macro_assets": macro_assets,
        "market_internals": {
            "breadth_above_sma50_pct": breadth_above_sma50_pct,
            "rv10_rv60_ratio": rv10_rv60_ratio,
            "rs_vs_spy_top5": [{"ticker": r["ticker"], "rs_vs_spy": r["rs_vs_spy"], "1d_pct": r["change_pct_1d"]} for r in rs_sorted[:5]],
            "rs_vs_spy_bottom5": [{"ticker": r["ticker"], "rs_vs_spy": r["rs_vs_spy"], "1d_pct": r["change_pct_1d"]} for r in rs_sorted[-5:]],
            "residual_momentum_top5": (latest_rmp.get("leaders_top10") or [])[:5],
            "volume_ratio_top5": [{"ticker": r["ticker"], "volume_ratio": r["volume_ratio"], "1d_pct": r["change_pct_1d"]} for r in vol_sorted[:5]],
            "rsi_overbought": rsi_overbought,
            "rsi_oversold": rsi_oversold,
            "near_52w_high": near_52w_high,
        },
        "active_ticker_snapshots": active_ticker_snapshots,
        "headlines": headlines,
    }


def save_market_summary(summary: Dict[str, Any], state_dir: Path = STATE_DIR) -> Path:
    path = state_dir / "last_market_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Stage 1: 100% Python Pre-Calculation of All Report Numbers & Statistics
# ---------------------------------------------------------------------------

def _compute_daily_returns_by_date(state: AgentLedgerState) -> Dict[str, float]:
    """Extract end-of-day equity per trading_date_et and compute daily percentage returns."""
    eod_eq: Dict[str, float] = {}
    for pt in state.equity_curve:
        if pt.trading_date_et:
            eod_eq[pt.trading_date_et] = float(pt.equity)
    dates = sorted(eod_eq.keys())
    rets: Dict[str, float] = {}
    prev_eq = state.initial_cash if state.initial_cash > 0 else INITIAL_CASH
    for d in dates:
        eq = eod_eq[d]
        rets[d] = ((eq - prev_eq) / prev_eq) * 100.0 if prev_eq > 0 else 0.0
        prev_eq = eq
    return rets


def _compute_final_10day_stats(
    agent_states: List[AgentLedgerState],
    leaderboard: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute rigorous paired excess-return Student-t statistics vs SPY for Day-10 final evaluation."""
    spy_state = next((s for s in agent_states if s.agent_id == "ctrl_spy"), None)
    spy_daily = _compute_daily_returns_by_date(spy_state) if spy_state else {}

    per_agent: List[Dict[str, Any]] = []
    for row in leaderboard:
        aid = row["agent_id"]
        if aid == "ctrl_spy":
            continue
        st = next((s for s in agent_states if s.agent_id == aid), None)
        if not st:
            continue
        ag_daily = _compute_daily_returns_by_date(st)
        common_dates = sorted(set(ag_daily.keys()) & set(spy_daily.keys()))
        diffs = [ag_daily[d] - spy_daily[d] for d in common_dates]
        n = len(diffs)
        if n >= 2:
            mean_d = sum(diffs) / n
            sd = math.sqrt(sum((x - mean_d) ** 2 for x in diffs) / (n - 1))
            se = sd / math.sqrt(n) if sd > 0 else 0.0
            t_stat = (mean_d / se) if se > 1e-9 else None
            t_crit = _T_CRIT_95.get(n - 1, 2.2622)
            ci_lo = round(mean_d - t_crit * se, 3) if se > 0 else round(mean_d, 3)
            ci_hi = round(mean_d + t_crit * se, 3) if se > 0 else round(mean_d, 3)
            contains_zero = (ci_lo <= 0.0 <= ci_hi)
            per_agent.append(
                {
                    "agent_id": aid,
                    "name": row["name"],
                    "group": row["group"],
                    "n_days": n,
                    "mean_daily_excess_pct": round(mean_d, 3),
                    "std_daily_excess_pct": round(sd, 3),
                    "t_stat": round(t_stat, 3) if t_stat is not None else None,
                    "ci_95_pct": [ci_lo, ci_hi],
                    "ci_contains_zero": contains_zero,
                    "statistically_significant": not contains_zero,
                }
            )
        else:
            per_agent.append(
                {
                    "agent_id": aid,
                    "name": row["name"],
                    "group": row["group"],
                    "n_days": n,
                    "mean_daily_excess_pct": None,
                    "std_daily_excess_pct": None,
                    "t_stat": None,
                    "ci_95_pct": None,
                    "ci_contains_zero": True,
                    "statistically_significant": False,
                }
            )

    return {
        "n_trading_days": len(spy_daily),
        "student_t_critical_95": _T_CRIT_95.get(max(1, len(spy_daily) - 1), 2.2622),
        "per_agent_vs_spy": per_agent,
    }


def _ts_to_et_date(ts: str) -> str:
    if not ts:
        return ""
    try:
        return to_et_datetime(ts).strftime("%Y-%m-%d")
    except Exception:
        return ts[:10]


def build_report_payload(
    agent_states: List[AgentLedgerState],
    competition_state: Dict[str, Any],
    trading_date_et: str,
    trading_day_number: int,
    market_summary: Optional[Dict[str, Any]] = None,
    claude_rmp_logs: Optional[List[Dict[str, Any]]] = None,
    claude_rmp_state: Optional[Dict[str, Any]] = None,
    cycles_run_today: Optional[int] = None,
) -> Dict[str, Any]:
    """Pre-calculate every number required for the daily Markdown report in Python (zero LLM math)."""
    spy_state = next((s for s in agent_states if s.agent_id == "ctrl_spy"), None)
    rnd_state = next((s for s in agent_states if s.agent_id == "ctrl_random"), None)

    spy_cum_pct = ((spy_state.equity - spy_state.initial_cash) / spy_state.initial_cash * 100.0) if spy_state else 0.0
    rnd_cum_pct = ((rnd_state.equity - rnd_state.initial_cash) / rnd_state.initial_cash * 100.0) if rnd_state else 0.0
    spy_daily_pct = float(spy_state.daily_pnl_pct) if spy_state else 0.0

    # Infer actual cycles run today from equity curve timestamps if not explicitly supplied
    if cycles_run_today is None:
        day_timestamps = {
            pt.timestamp
            for st in agent_states
            for pt in st.equity_curve
            if pt.trading_date_et == trading_date_et and pt.timestamp
        }
        cycles_run_today = len(day_timestamps) if day_timestamps else 0

    leaderboard: List[Dict[str, Any]] = []
    today_trades: List[Dict[str, Any]] = []
    today_rejections: List[Dict[str, Any]] = []
    open_positions_rows: List[Dict[str, Any]] = []

    for st in agent_states:
        spec = AGENT_SPECS_BY_ID.get(st.agent_id)
        group = classify_agent_group(st.agent_id, st.category)
        m = compute_leaderboard_metrics(st, spy_return_pct=spy_cum_pct)

        # Daily $ and % from day_start_equity (or journal for trading_date_et)
        day_journal = next((j for j in reversed(st.daily_journal) if j.trading_date_et == trading_date_et), None)
        if day_journal:
            daily_pnl_dollars = round(float(day_journal.daily_pnl), 2)
            daily_pnl_pct = round(float(day_journal.daily_pnl_pct), 2)
        else:
            start_eq = st.day_start_equity if st.day_start_equity > 0 else st.initial_cash
            daily_pnl_dollars = round(st.equity - start_eq, 2)
            daily_pnl_pct = round(((st.equity - start_eq) / start_eq) * 100.0, 2) if start_eq > 0 else 0.0

        cum_pct = round(float(m["profit_pct"]), 2)
        vs_spy_pp = round(cum_pct - spy_cum_pct, 2)
        vs_rnd_pp = round(cum_pct - rnd_cum_pct, 2)

        closed_sells = [t for t in st.trades if t.side == "SELL" and t.realized_pnl is not None]
        closed_cnt = len(closed_sells)
        gross_profit = sum(float(t.realized_pnl) for t in closed_sells if float(t.realized_pnl) > 0)
        gross_loss = sum(abs(float(t.realized_pnl)) for t in closed_sells if float(t.realized_pnl) < 0)
        if closed_cnt == 0:
            win_rate_val = None
            pf_display = "N/A"
            avg_win_val = None
            avg_loss_val = None
        else:
            wins = [float(t.realized_pnl) for t in closed_sells if float(t.realized_pnl) > 0]
            losses = [float(t.realized_pnl) for t in closed_sells if float(t.realized_pnl) < 0]
            win_rate_val = round((len(wins) / closed_cnt) * 100.0, 1)
            if gross_loss == 0.0:
                pf_display = "∞" if gross_profit > 0 else "N/A"
            else:
                pf_display = f"{gross_profit / gross_loss:.2f}"
            avg_win_val = round(sum(wins) / len(wins), 2) if wins else None
            avg_loss_val = round(sum(losses) / len(losses), 2) if losses else None

        total_friction = round(sum(float(t.spread_cost_dollars) + float(t.slippage_cost_dollars) for t in st.trades), 2)
        day_rejs = [r for r in st.rejections if _ts_to_et_date(r.timestamp) == trading_date_et]

        # Buffer to -10.0% BLOWN_UP circuit breaker (based on peak-to-trough current_drawdown_pct)
        cb_buffer_pp = round(float(st.current_drawdown_pct) - (-10.0), 2)

        leaderboard.append(
            {
                "rank": 0,
                "agent_id": st.agent_id,
                "name": st.name,
                "group": group,
                "equity": round(float(st.equity), 2),
                "cash": round(float(st.cash), 2),
                "daily_pnl_dollars": daily_pnl_dollars,
                "daily_pnl_pct": daily_pnl_pct,
                "cum_profit_dollars": round(float(st.equity - st.initial_cash), 2),
                "cum_return_pct": cum_pct,
                "vs_spy_pp": vs_spy_pp,
                "vs_random_pp": vs_rnd_pp,
                "mdd_pct": round(float(st.max_drawdown_pct), 2),
                "current_dd_pct": round(float(st.current_drawdown_pct), 2),
                "circuit_breaker_buffer_pp": cb_buffer_pp,
                "total_fills": len(st.trades),
                "closed_trades": closed_cnt,
                "win_rate_pct": win_rate_val,
                "profit_factor_str": pf_display,
                "avg_win": avg_win_val,
                "avg_loss": avg_loss_val,
                "total_friction": total_friction,
                "rejections_today": len(day_rejs),
                "rejections_total": len(st.rejections),
                "status": st.status,
                "status_reason": st.status_reason,
            }
        )

        # Collect today's trades (strictly in US/Eastern date)
        for tr in st.trades:
            if _ts_to_et_date(tr.fill_timestamp) == trading_date_et or _ts_to_et_date(tr.decision_timestamp) == trading_date_et:
                dec_et = to_et_datetime(tr.decision_timestamp).strftime("%H:%M") if tr.decision_timestamp else "-"
                fill_et = to_et_datetime(tr.fill_timestamp).strftime("%H:%M") if tr.fill_timestamp else "-"
                today_trades.append(
                    {
                        "agent_id": st.agent_id,
                        "agent_name": st.name,
                        "ticker": tr.ticker,
                        "side": tr.side,
                        "shares": round(float(tr.shares), 4),
                        "decision_to_fill_et": f"{dec_et}→{fill_et}",
                        "ref_price": round(float(tr.reference_price), 2),
                        "fill_price": round(float(tr.fill_price), 2),
                        "friction": round(float(tr.spread_cost_dollars) + float(tr.slippage_cost_dollars), 2),
                        "realized_pnl": round(float(tr.realized_pnl), 2) if tr.realized_pnl is not None else None,
                        "realized_pnl_pct": round(float(tr.realized_pnl_pct), 2) if tr.realized_pnl_pct is not None else None,
                        "exit_reason": tr.exit_reason or "AGENT_SIGNAL",
                        "confidence": round(float(tr.confidence), 2),
                        "reasoning": tr.reasoning,
                    }
                )

        # Collect today's rejections
        for rej in day_rejs:
            today_rejections.append(
                {
                    "agent_id": st.agent_id,
                    "agent_name": st.name,
                    "timestamp": rej.timestamp,
                    "action": rej.action,
                    "ticker": rej.ticker,
                    "reason": rej.reason,
                }
            )

        # Collect open positions with exact stop & take-profit distance %
        for sym, pos in sorted(st.positions.items()):
            if pos.shares <= 0:
                continue
            cur_px = float(pos.current_price) if pos.current_price > 0 else float(pos.avg_entry_price)
            stop_dist_pct = round(((pos.stop_loss - cur_px) / cur_px) * 100.0, 2) if (pos.stop_loss and cur_px > 0) else None
            tp_dist_pct = round(((pos.take_profit - cur_px) / cur_px) * 100.0, 2) if (pos.take_profit and cur_px > 0) else None
            open_positions_rows.append(
                {
                    "agent_id": st.agent_id,
                    "agent_name": st.name,
                    "ticker": sym,
                    "shares": round(float(pos.shares), 4),
                    "entry_price": round(float(pos.avg_entry_price), 2),
                    "current_price": round(cur_px, 2),
                    "market_value": round(float(pos.market_value), 2),
                    "weight_pct": round((float(pos.market_value) / st.equity) * 100.0, 1) if st.equity > 0 else 0.0,
                    "unrealized_pnl": round(float(pos.unrealized_pnl), 2),
                    "unrealized_pnl_pct": round(float(pos.unrealized_pnl_pct), 2),
                    "stop_loss": round(float(pos.stop_loss), 2) if pos.stop_loss else None,
                    "stop_dist_pct": stop_dist_pct,
                    "take_profit": round(float(pos.take_profit), 2) if pos.take_profit else None,
                    "tp_dist_pct": tp_dist_pct,
                }
            )

    # Sort leaderboard strictly by Equity descending
    leaderboard.sort(key=lambda r: (r["equity"], r["cum_return_pct"], -abs(r["mdd_pct"])), reverse=True)
    for idx, row in enumerate(leaderboard, start=1):
        row["rank"] = idx

    # Strategy group aggregates (exact 4 groups)
    group_order = ["코딩 기반 (5)", "Gemini Flash LLM (4)", "공시 스타일 학습 (2)", "SPY / 랜덤 (2)"]
    group_summary: List[Dict[str, Any]] = []
    for gname in group_order:
        members = [r for r in leaderboard if r["group"] == gname]
        if not members:
            continue
        daily_avg = round(sum(m["daily_pnl_pct"] for m in members) / len(members), 2)
        cum_avg = round(sum(m["cum_return_pct"] for m in members) / len(members), 2)
        cum_med = round(float(statistics.median(m["cum_return_pct"] for m in members)), 2)
        best_m = max(members, key=lambda x: x["equity"])
        worst_m = min(members, key=lambda x: x["equity"])
        group_summary.append(
            {
                "group": gname,
                "count": len(members),
                "avg_daily_pct": daily_avg,
                "avg_cum_pct": cum_avg,
                "median_cum_pct": cum_med,
                "best_agent": f"{best_m['name']} ({best_m['cum_return_pct']:+.2f}%)",
                "worst_agent": f"{worst_m['name']} ({worst_m['cum_return_pct']:+.2f}%)",
                "total_fills": sum(m["total_fills"] for m in members),
                "avg_friction": round(sum(m["total_friction"] for m in members) / len(members), 2),
                "total_rejections": sum(m["rejections_total"] for m in members),
            }
        )

    # Agent 14 (`claude_rmp`) telemetry summary
    rmp_logs_today = [lg for lg in (claude_rmp_logs or []) if lg.get("trading_date_et") == trading_date_et]
    latest_rmp = (rmp_logs_today[-1] if rmp_logs_today else (claude_rmp_logs[-1] if claude_rmp_logs else {})) or {}
    rmp_agent = next((s for s in agent_states if s.agent_id == "claude_rmp"), None)
    rmp_trades = list(rmp_agent.trades) if rmp_agent else []
    rmp_closed = [t for t in rmp_trades if t.side == "SELL" and t.realized_pnl is not None]
    rmp_r_vals: List[float] = []
    last_buy_risk_per_share: Dict[str, float] = {}
    for t in rmp_trades:
        if t.side == "BUY" and t.stop_loss and t.fill_price and t.fill_price > t.stop_loss:
            last_buy_risk_per_share[t.ticker] = float(t.fill_price - t.stop_loss)
        elif t.side == "SELL" and t.realized_pnl is not None:
            rps = last_buy_risk_per_share.get(t.ticker)
            if (not rps or rps <= 0) and t.stop_loss and t.reference_price and abs(t.reference_price - t.stop_loss) > 0:
                rps = abs(float(t.reference_price) - float(t.stop_loss))
            if rps and rps > 0 and t.shares > 0:
                rmp_r_vals.append(float(t.realized_pnl) / (rps * float(t.shares)))
    rmp_avg_r = round(sum(rmp_r_vals) / len(rmp_r_vals), 2) if rmp_r_vals else None

    exp_log = (claude_rmp_state or {}).get("regime_exposure_log") or []
    up_exps = [float(e["exposure_pct"]) for e in exp_log if e.get("spy_up") is True]
    dn_exps = [float(e["exposure_pct"]) for e in exp_log if e.get("spy_up") is False]
    rmp_eq = rmp_agent.equity if rmp_agent and rmp_agent.equity > 0 else INITIAL_CASH
    heat_d = float(latest_rmp.get("heat_dollars") or 0.0)
    heat_cap_d = float(latest_rmp.get("heat_cap_dollars") or 0.0)

    claude_rmp_telemetry = {
        "regime_score": latest_rmp.get("regime_score"),
        "regime_flags": latest_rmp.get("regime_flags") or {},
        "slots": latest_rmp.get("slots"),
        "heat_dollars": round(heat_d, 2),
        "heat_cap_dollars": round(heat_cap_d, 2),
        "heat_pct_of_equity": round((heat_d / rmp_eq) * 100.0, 2) if rmp_eq > 0 else 0.0,
        "heat_cap_pct_of_equity": round((heat_cap_d / rmp_eq) * 100.0, 2) if rmp_eq > 0 else 0.0,
        "average_r": rmp_avg_r,
        "closed_trades_count": len(rmp_closed),
        "leaders_top10": latest_rmp.get("leaders_top10") or [],
        "today_action_codes": [
            a.get("code") for lg in rmp_logs_today for a in (lg.get("actions") or []) if a.get("code")
        ],
        "spy_up_avg_exposure_pct": round(sum(up_exps) / len(up_exps), 1) if up_exps else None,
        "spy_down_avg_exposure_pct": round(sum(dn_exps) / len(dn_exps), 1) if dn_exps else None,
        "exposure_sample_days": len(exp_log),
    }

    is_final_day = (
        trading_day_number >= COMPETITION_TRADING_DAYS
        or str(competition_state.get("status", "")).upper() == "COMPLETED"
    )

    session_open_et = (market_summary or {}).get("session_open_et", "09:30")
    session_close_et = (market_summary or {}).get("session_close_et", "16:00")
    session_type = (
        f"Early Close ({session_open_et}-{session_close_et} ET)"
        if session_close_et != "16:00"
        else f"Regular ({session_open_et}-{session_close_et} ET)"
    )

    active_cnt = sum(1 for r in leaderboard if r["status"] in ("ACTIVE", "COMPLETED"))
    halt_cnt = sum(1 for r in leaderboard if r["status"] == "DAILY_HALTED")
    blown_cnt = sum(1 for r in leaderboard if r["status"] == "BLOWN_UP")
    beating_spy = sum(1 for r in leaderboard if r["agent_id"] != "ctrl_spy" and r["cum_return_pct"] > spy_cum_pct)
    beating_rnd = sum(1 for r in leaderboard if r["agent_id"] != "ctrl_random" and r["cum_return_pct"] > rnd_cum_pct)

    friction_top3 = sorted(leaderboard, key=lambda x: x["total_friction"], reverse=True)[:3]
    cb_watch = [
        r for r in leaderboard
        if r["circuit_breaker_buffer_pp"] <= 3.0 or r["daily_pnl_pct"] <= -2.0 or r["status"] in ("DAILY_HALTED", "BLOWN_UP")
    ]

    # Sort open positions by closest stop-loss distance first
    open_positions_rows.sort(key=lambda p: abs(p["stop_dist_pct"]) if p["stop_dist_pct"] is not None else 999.0)

    final_stats = _compute_final_10day_stats(agent_states, leaderboard) if is_final_day else None

    return {
        "metadata": {
            "date_et": trading_date_et,
            "trading_day": trading_day_number,
            "total_trading_days": COMPETITION_TRADING_DAYS,
            "is_final_day": is_final_day,
            "session_type": session_type,
            "cycles_run_today": cycles_run_today,
            "next_trading_day_info": _get_next_trading_day_info(trading_date_et),
            "active_count": active_cnt,
            "halt_count": halt_cnt,
            "blown_up_count": blown_cnt,
            "spy_daily_pct": round(spy_daily_pct, 2),
            "spy_cum_pct": round(spy_cum_pct, 2),
            "beating_spy_count": beating_spy,
            "beating_random_count": beating_rnd,
            "total_agents": len(leaderboard),
        },
        "market_summary": market_summary or {},
        "leaderboard": leaderboard,
        "group_summary": group_summary,
        "claude_rmp_telemetry": claude_rmp_telemetry,
        "today_trades": today_trades,
        "today_rejections": today_rejections,
        "friction_top3": [
            {
                "name": f["name"],
                "total_friction": f["total_friction"],
                "pct_of_initial": round((f["total_friction"] / INITIAL_CASH) * 100.0, 2),
                "total_fills": f["total_fills"],
            }
            for f in friction_top3
        ],
        "open_positions": open_positions_rows[:25],
        "circuit_breaker_watch": cb_watch,
        "final_10day_stats": final_stats,
    }


# ---------------------------------------------------------------------------
# Stage 3A: Deterministic GFM Markdown Renderer (100% Math-Exact Fallback)
# ---------------------------------------------------------------------------

def render_deterministic_markdown_report(payload: Dict[str, Any]) -> str:
    """Render the exact GFM daily report structure deterministically from pre-computed JSON."""
    meta = payload["metadata"]
    mkt = payload.get("market_summary") or {}
    lb = payload["leaderboard"]
    groups = payload["group_summary"]
    rmp = payload["claude_rmp_telemetry"]
    trades = payload["today_trades"]
    rejs = payload["today_rejections"]
    open_pos = payload["open_positions"]
    f_top3 = payload["friction_top3"]
    cb_watch = payload["circuit_breaker_watch"]
    is_final = bool(meta["is_final_day"])

    best_day = max(lb, key=lambda r: r["daily_pnl_pct"])
    worst_day = min(lb, key=lambda r: r["daily_pnl_pct"])
    best_grp = max(groups, key=lambda g: g["avg_daily_pct"]) if groups else None
    worst_grp = min(groups, key=lambda g: g["avg_daily_pct"]) if groups else None

    reg_score_str = f"{rmp['regime_score']}/4" if rmp.get("regime_score") is not None else "N/A"
    lines: List[str] = [
        f"# AI Trading Arena 일일 리포트 — Day {meta['trading_day']}/10 ({meta['date_et']})",
        "",
        (
            f"> **세션 정보**: {meta['session_type']} (실행 사이클: {meta['cycles_run_today']}/13) | "
            f"**시장 레짐(14번 기준)**: Score `{reg_score_str}` | "
            f"**SPY 일일/누적**: `{_fmt_signed_pct(meta['spy_daily_pct'])}` / `{_fmt_signed_pct(meta['spy_cum_pct'])}` | "
            f"**활성 에이전트**: `{meta['active_count']}/{meta['total_agents']}` "
            f"(정지 `{meta['halt_count']}`, 탈락 `{meta['blown_up_count']}`)"
        ),
        "",
        "---",
        "",
        "## 1. 오늘의 핵심 요약 (Executive Summary)",
        f"- **일일 성과 Top / Bottom**: 최고 수익은 **{best_day['name']}** (`{_fmt_signed_pct(best_day['daily_pnl_pct'])}`, `{_fmt_money(best_day['daily_pnl_dollars'], signed=True)}`), 최저 수익은 **{worst_day['name']}** (`{_fmt_signed_pct(worst_day['daily_pnl_pct'])}`, `{_fmt_money(worst_day['daily_pnl_dollars'], signed=True)}`)입니다.",
        f"- **벤치마크 비교**: 누적 기준 `9. Buy-and-Hold SPY` 상회 에이전트 `{meta['beating_spy_count']}/{meta['total_agents'] - 1}명`, `10. Random Trader` 상회 에이전트 `{meta['beating_random_count']}/{meta['total_agents'] - 1}명`입니다.",
    ]
    if best_grp and worst_grp:
        lines.append(
            f"- **전략군 흐름**: 당일 가장 강세인 전략군은 **{best_grp['group']}** (당일 평균 `{_fmt_signed_pct(best_grp['avg_daily_pct'])}`), 가장 약세인 전략군은 **{worst_grp['group']}** (당일 평균 `{_fmt_signed_pct(worst_grp['avg_daily_pct'])}`)입니다."
        )
    lines.append(
        f"- **리스크·이상 징후**: 당일 신규 체결 `{len(trades)}건`, 리스크 관리자 거부 `{len(rejs)}건`, 일일 정지(`DAILY_HALTED`) `{meta['halt_count']}명`, 영구 탈락(`BLOWN_UP`) `{meta['blown_up_count']}명`입니다."
    )

    # Section 2: Market Macro & Technical Regime
    lines.extend([
        "",
        "---",
        "",
        "## 2. 시장 매크로 및 기술적 레짐 브리핑",
        "",
        "### 2.1 주요 벤치마크 및 매크로 자산 흐름",
        "| Ticker | 종가 ($) | 일일 등락 (%) | ORB(첫 30분) 대비 위치 | SMA(20) / SMA(50) 대비 | RSI(14) | Volume Ratio | 핵심 흐름 요약 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])
    macro = mkt.get("macro_assets") or {}
    for sym in ("SPY", "QQQ", "TLT", "GLD"):
        r = macro.get(sym)
        if not r:
            lines.append(f"| **{sym}** | N/A | N/A | N/A | N/A | N/A | N/A | 당일 스냅샷 미수집 |")
        else:
            vs20 = _fmt_signed_pct(r.get("vs_sma20_pct")) if r.get("vs_sma20_pct") is not None else "N/A"
            vs50 = _fmt_signed_pct(r.get("vs_sma50_pct")) if r.get("vs_sma50_pct") is not None else "N/A"
            lines.append(
                f"| **{sym}** | {_fmt_money(r['close'])} | {_fmt_signed_pct(r['change_pct_1d'])} | "
                f"{r.get('orb_position', 'N/A')} | {vs20} / {vs50} | {_fmt_num(r.get('rsi_14'), '{:.1f}')} | "
                f"{_fmt_num(r.get('volume_ratio'), '{:.2f}x')} | {r.get('bb_position', 'N/A')} (5일 {_fmt_signed_pct(r.get('change_pct_5d'))}) |"
            )

    internals = mkt.get("market_internals") or {}
    breadth_str = f"{internals['breadth_above_sma50_pct']:.1f}%" if internals.get("breadth_above_sma50_pct") is not None else "N/A"
    rv_str = f"{internals['rv10_rv60_ratio']:.2f}" if internals.get("rv10_rv60_ratio") is not None else "N/A"
    rs_top = ", ".join(f"{x['ticker']}({x['rs_vs_spy']:+.2f})" for x in (internals.get("rs_vs_spy_top5") or [])) or "N/A"
    rm_top = ", ".join(f"{x['ticker']}({x['score']:.2f})" for x in (internals.get("residual_momentum_top5") or [])) or "N/A"
    vol_top = ", ".join(f"{x['ticker']}({x['volume_ratio']:.2f}x)" for x in (internals.get("volume_ratio_top5") or [])) or "N/A"
    ob_str = ", ".join(f"{x['ticker']}(RSI {x['rsi_14']:.1f})" for x in (internals.get("rsi_overbought") or [])) or "없음"
    os_str = ", ".join(f"{x['ticker']}(RSI {x['rsi_14']:.1f})" for x in (internals.get("rsi_oversold") or [])) or "없음"
    hi52_str = ", ".join(f"{x['ticker']}({x['dist_52w_pct']:+.1f}%)" for x in (internals.get("near_52w_high") or [])) or "N/A"
    headlines = mkt.get("headlines") or []

    lines.extend([
        "",
        "### 2.2 시장 내부 구조 (Breadth · 변동성 · 모멘텀 · 뉴스)",
        f"- **시장 폭(Breadth) 및 변동성**: 개별주 중 `SMA(50)` 상회 종목 비율 `{breadth_str}`, `RV10/RV60` 변동성 비율 `{rv_str}`.",
        f"- **상대강도(`RS vs SPY`) 상위 / 63일 잔차 모멘텀 상위**: RS 상위 `{rs_top}` | 잔차 모멘텀 상위 `{rm_top}`.",
        f"- **과열/과매도 및 거래량 급증**: RSI(14)≥70 `{ob_str}` / RSI(14)≤30 `{os_str}` / 거래량 비율 상위 `{vol_top}`.",
        f"- **52주 고점 근접 종목 (11·12번 기준)**: `{hi52_str}`.",
    ])
    if headlines:
        hl_txt = "; ".join(f"[{','.join(h.get('symbols') or ['MKT'])}] {h['headline']}" for h in headlines[:5])
        lines.append(f"- **당일 주요 뉴스(Alpaca Headlines)**: {hl_txt}")
    else:
        lines.append("- **당일 주요 뉴스(Alpaca Headlines)**: 주요 특이 뉴스 없음")

    # Section 3: Full 13-Agent Leaderboard
    lines.extend([
        "",
        "---",
        "",
        f"## 3. 전체 에이전트 성과 리더보드 ({meta['total_agents']}개 에이전트)",
        "",
        "| 순위 | 에이전트 (전략군) | 평가액 ($) | 일일 손익 ($ / %) | 누적 수익률 (%) | vs SPY (%p) | vs Random (%p) | MDD (%) | 체결 / 청산 | 승률 (%) | PF | 평균익 / 평균손 ($) | 마찰비용 ($) | 거부 | 상태 |",
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])
    for r in lb:
        is_ctrl = r["agent_id"] in ("ctrl_spy", "ctrl_random")
        label = f"**{r['name']}** ({r['group']})" if is_ctrl else f"{r['name']} ({r['group']})"
        wr = f"{r['win_rate_pct']:.1f}%" if r["win_rate_pct"] is not None else "N/A"
        aw = _fmt_money(r["avg_win"], signed=True) if r["avg_win"] is not None else "N/A"
        al = _fmt_money(r["avg_loss"], signed=True) if r["avg_loss"] is not None else "N/A"
        lines.append(
            f"| {r['rank']} | {label} | {_fmt_money(r['equity'])} | "
            f"{_fmt_money(r['daily_pnl_dollars'], signed=True)} / {_fmt_signed_pct(r['daily_pnl_pct'])} | "
            f"{_fmt_signed_pct(r['cum_return_pct'])} | {_fmt_signed_pct(r['vs_spy_pp'], '%p')} | "
            f"{_fmt_signed_pct(r['vs_random_pp'], '%p')} | {r['mdd_pct']:.2f}% | "
            f"{r['total_fills']} / {r['closed_trades']} | {wr} | {r['profit_factor_str']} | "
            f"{aw} / {al} | {_fmt_money(r['total_friction'])} | {r['rejections_total']} | `{r['status']}` |"
        )

    # Section 4: Cohort Comparison
    lines.extend([
        "",
        "---",
        "",
        "## 4. 전략군별 성과 비교",
        "",
        "| 전략군 (인원) | 당일 평균 수익률 (%) | 누적 평균 수익률 (%) | 누적 중앙값 (%) | 최고 / 최저 에이전트 | 총 체결 수 | 평균 마찰비용 ($) | 총 리스크 거부 |",
        "| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: |",
    ])
    for g in groups:
        lines.append(
            f"| {g['group']} | {_fmt_signed_pct(g['avg_daily_pct'])} | {_fmt_signed_pct(g['avg_cum_pct'])} | "
            f"{_fmt_signed_pct(g['median_cum_pct'])} | {g['best_agent']} / {g['worst_agent']} | "
            f"{g['total_fills']} | {_fmt_money(g['avg_friction'])} | {g['total_rejections']} |"
        )
    lines.append("")
    if best_grp and worst_grp:
        lines.append(
            f"- **그룹 간 격차 분석**: 당일 `{best_grp['group']}`이 평균 `{_fmt_signed_pct(best_grp['avg_daily_pct'])}`로 선두를 기록했고, "
            f"`{worst_grp['group']}`은 평균 `{_fmt_signed_pct(worst_grp['avg_daily_pct'])}`를 기록했습니다. 누적 마찰비용은 체결 빈도가 높은 전략군일수록 수익률 차감 폭이 크게 나타났습니다."
        )

    # Section 5: Agent 14 Special Telemetry
    avg_r_str = f"{rmp['average_r']:+.2f} R" if rmp.get("average_r") is not None else "N/A"
    up_e = f"{rmp['spy_up_avg_exposure_pct']:.1f}%" if rmp.get("spy_up_avg_exposure_pct") is not None else "N/A"
    dn_e = f"{rmp['spy_down_avg_exposure_pct']:.1f}%" if rmp.get("spy_down_avg_exposure_pct") is not None else "N/A"
    act_codes = ", ".join(sorted(set(rmp.get("today_action_codes") or []))) or "HOLD"
    lines.extend([
        "",
        "---",
        "",
        "## 5. 스페셜 분석: 14번 `claude_rmp` 텔레메트리 및 가설 검증",
        "",
        "| 항목 | 당일 수치 / 상태 | 비고 |",
        "| :--- | :--- | :--- |",
        f"| **레짐 점수 (`0~4`) 및 허용 슬롯** | `Score {reg_score_str}` (허용 슬롯: `{rmp.get('slots', 'N/A')}`개) | C1~C4: `{rmp.get('regime_flags')}` |",
        f"| **포트폴리오 리스크 (`Heat / HeatCap`)** | `{rmp['heat_pct_of_equity']:.2f}% / {rmp['heat_cap_pct_of_equity']:.2f}%` | `${rmp['heat_dollars']:.2f} / ${rmp['heat_cap_dollars']:.2f}` |",
        f"| **거래당 평균 R (`Average R`)** | `{avg_r_str}` (청산 `{rmp['closed_trades_count']}`건 기준) | 누적 기준 |",
        f"| **필터·선정 지표 작동** | 당일 사유 코드: `{act_codes}` | Top-5 리더: `{rm_top}` |",
        "",
        f"- **가설 H1~H3 반증 트래커 (SPY 상승일 vs 하락일 노출도)**: 누적 `{rmp['exposure_sample_days']}일` 표본 기준 SPY 상승일 평균 노출도 `{up_e}` vs SPY 하락일 평균 노출도 `{dn_e}`. 단기 표본(`Day {meta['trading_day']}/10`)이므로 통계적 확정은 10거래일 종료 후 판정합니다.",
    ])

    # Section 6: Trade Review, Rejections & Friction
    lines.extend([
        "",
        "---",
        "",
        "## 6. 당일 매매 복기, 리스크 거부 및 마찰 감사",
        "",
        "### 6.1 주요 진입·청산 복기 (Best & Worst / 특이 체결)",
        "| 에이전트 | 종목 | 구분 | 결정→체결 시각 (ET) | 기준가→체결가 ($) | 실현손익 ($ / %) | 신뢰도 | 근거 및 지표 정합성 평가 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])
    if trades:
        # Highlight notable trades: closed trades sorted by absolute PnL first, then entries (up to 15 rows)
        sorted_tr = sorted(
            trades,
            key=lambda x: (0 if x["realized_pnl"] is not None else 1, -abs(x["realized_pnl"] or 0.0)),
        )[:15]
        for tr in sorted_tr:
            rpnl = (
                f"{_fmt_money(tr['realized_pnl'], signed=True)} ({_fmt_signed_pct(tr['realized_pnl_pct'])})"
                if tr["realized_pnl"] is not None
                else "-"
            )
            reason_clean = str(tr["reasoning"] or tr["exit_reason"]).replace("|", "/").replace("\n", " ")[:140]
            lines.append(
                f"| {tr['agent_name']} | {tr['ticker']} | `{tr['side']}` | {tr['decision_to_fill_et']} | "
                f"{_fmt_money(tr['ref_price'])}→{_fmt_money(tr['fill_price'])} | {rpnl} | {tr['confidence']:.2f} | {reason_clean} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | 당일 신규 체결 없음 |")

    losses_today = [t for t in trades if t["realized_pnl"] is not None and t["realized_pnl"] < 0]
    if losses_today:
        loss_reasons = ", ".join(f"{t['agent_name']} {t['ticker']}({t['exit_reason']}, {_fmt_money(t['realized_pnl'], signed=True)})" for t in losses_today[:5])
        lines.append(f"\n- **주요 손실 원인 진단**: 당일 실현 손실 거래는 총 `{len(losses_today)}건` ({loss_reasons})으로, 손절선 터치·청산 시그널 및 호가 스프레드+5 bps 체결 마찰이 반영되었습니다.")
    else:
        lines.append("\n- **주요 손실 원인 진단**: 당일 실현 손실 청산 거래는 없습니다.")

    lines.extend(["", "### 6.2 리스크 관리자 거부 로그 (`rejections`) 감사"])
    if rejs:
        lines.append(f"- 당일 총 거부 건수: **`{len(rejs)}건`**")
        for rj in rejs[:10]:
            lines.append(f"  - `{rj['agent_name']}` — `{rj['action']} {rj['ticker']}` 거부 사유: {rj['reason']}")
    else:
        lines.append("- 당일 리스크 관리자 거부 건수 없음.")

    lines.extend(["", "### 6.3 거래 마찰 비용 (스프레드 + 5 bps 슬리피지) 영향"])
    for ft in f_top3:
        lines.append(
            f"- **{ft['name']}**: 누적 마찰 비용 `{_fmt_money(ft['total_friction'])}` "
            f"(초기 자금 `$10,000` 대비 `{ft['pct_of_initial']:.2f}%`, 총 `{ft['total_fills']}회` 체결)"
        )

    # Section 7: Next Trading Day Watchlist (Monitoring Only — NO Strategy Change)
    lines.extend([
        "",
        "---",
        "",
        "## 7. 다음 거래일 관전 포인트 (모니터링 전용 · 전략 수정 금지)",
        "",
        "### 7.1 현재 보유(오픈) 포지션 손절·익절 근접도",
        "| 에이전트 | 종목 | 진입가 ($) | 현재가 ($) | 미실현 손익 ($ / %) | 손절가 (잔여 거리 %) | 익절가 (잔여 거리 %) | 주시 포인트 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])
    if open_pos:
        for p in open_pos[:15]:
            sl_str = f"{_fmt_money(p['stop_loss'])} ({_fmt_signed_pct(p['stop_dist_pct'])})" if p["stop_loss"] else "N/A"
            tp_str = f"{_fmt_money(p['take_profit'])} ({_fmt_signed_pct(p['tp_dist_pct'])})" if p["take_profit"] else "Trailing / N/A"
            note = "손절선 근접 주시" if (p["stop_dist_pct"] is not None and abs(p["stop_dist_pct"]) <= 1.5) else f"비중 {p['weight_pct']:.1f}%"
            lines.append(
                f"| {p['agent_name']} | {p['ticker']} | {_fmt_money(p['entry_price'])} | {_fmt_money(p['current_price'])} | "
                f"{_fmt_money(p['unrealized_pnl'], signed=True)} ({_fmt_signed_pct(p['unrealized_pnl_pct'])}) | "
                f"{sl_str} | {tp_str} | {note} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | 현재 전 에이전트 보유 포지션 없음 (현금 100%) |")

    lines.extend(["", "### 7.2 서킷 브레이커 여유폭 및 레짐 분기점"])
    if cb_watch:
        cb_str = ", ".join(f"{c['name']} (누적 DD {c['current_dd_pct']:.2f}%, -10%까지 여유 {c['circuit_breaker_buffer_pp']:.2f}%p)" for c in cb_watch)
        lines.append(f"- **서킷 브레이커 경계 에이전트**: {cb_str}")
    else:
        lines.append("- **서킷 브레이커 경계 에이전트**: 현재 `-10%` 탈락(`BLOWN_UP`)까지 여유폭 3.0%p 이내 또는 `-2%` 이하 일일 손실에 근접한 에이전트 없음.")

    spy_m = macro.get("SPY") or {}
    lines.append(
        f"- **레짐 점수 변화 가능성**: 14번 `claude_rmp` 현재 레짐 `{reg_score_str}` (허용 슬롯 `{rmp.get('slots', 'N/A')}`개). "
        f"다음 거래일 `SPY` 종가 vs `SMA(50)` (`{_fmt_money(spy_m.get('sma_50'))}`), 개별주 Breadth `50%` 경계(`{breadth_str}`), `RV10/RV60` `1.25` 임계치(`{rv_str}`) 변동 여부를 주시합니다."
    )
    lines.append(f"- **일정 체크**: 다음 미국 거래일 — **{meta['next_trading_day_info']}**.")

    # Section 8: ONLY when is_final_day == True
    if is_final and payload.get("final_10day_stats"):
        fstats = payload["final_10day_stats"]
        n_days = fstats.get("n_trading_days", 10)
        lines.extend([
            "",
            "---",
            "",
            "## 8. 통계적 최종 평가: 운(Luck) vs 실력(Skill)",
            f"- **표본 크기의 한계 명시**: 10거래일(일별 수익률 표본 `N={n_days}`)은 통계적 검정력(Statistical Power)이 매우 낮아, 단기 분산(운)과 구조적 알파(실력)를 확정적으로 분리하기 어렵습니다.",
            "",
            "| 에이전트 (전략군) | 일일 초과수익 평균 (d̄, %p) | 표준편차 (s_d) | t-통계량 | 95% 신뢰구간 (%p) | 0 포함 여부 (통계적 유의성) |",
            "| :--- | :---: | :---: | :---: | :---: | :---: |",
        ])
        for row in fstats.get("per_agent_vs_spy") or []:
            ci = row.get("ci_95_pct")
            ci_str = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "N/A"
            sig_str = "0 미포함 (유의)" if row.get("statistically_significant") else "0 포함 (운 배제 불가)"
            lines.append(
                f"| {row['name']} ({row['group']}) | {_fmt_signed_pct(row.get('mean_daily_excess_pct'), '%p')} | "
                f"{_fmt_num(row.get('std_daily_excess_pct'), '{:.3f}')} | {_fmt_num(row.get('t_stat'), '{:+.3f}')} | "
                f"{ci_str} | {sig_str} |"
            )
        lines.append(
            "\n- **정직한 종합 판정**: 95% 신뢰구간에 0이 포함된 에이전트의 초과수익은 10거래일 단기 표본에서 통계적으로 우연과 구분되지 않으며, "
            "수익률 절대값보다 `10. Random Trader` 및 `9. SPY` 대비 일관된 MDD 통제와 거래 마찰 관리 역량을 중심으로 해석해야 합니다."
        )

    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------------
# Stage 3B: Post-Check Validator & Optional Gemini Flash Narrative Polish
# ---------------------------------------------------------------------------

def validate_report_markdown(
    markdown_text: str,
    agent_states: List[AgentLedgerState],
    is_final_day: bool,
) -> Tuple[bool, List[str]]:
    """Verify report structural integrity, 13-agent completeness, and Day-10 section guard."""
    errors: List[str] = []
    txt = markdown_text or ""
    if len(txt.strip()) < 400:
        errors.append("Report body too short (<400 chars).")

    for sec_num in range(1, 8):
        if f"## {sec_num}." not in txt:
            errors.append(f"Missing required section '## {sec_num}.'")

    has_sec8 = "## 8." in txt
    if is_final_day and not has_sec8:
        errors.append("Final day (is_final_day=True) requires section '## 8.'")
    if not is_final_day and has_sec8:
        errors.append("Non-final day (is_final_day=False) MUST omit section '## 8.'")

    for st in agent_states:
        if st.name not in txt and st.agent_id not in txt:
            errors.append(f"Missing agent '{st.name}' ({st.agent_id}) from report.")

    # Guard against forbidden strategy-tuning language
    forbidden_tuning = (
        "파라미터를 수정",
        "손절폭을 넓혀야",
        "RSI 기준을 조정",
        "로직을 변경해야",
        "전략을 수정해야",
    )
    for bad in forbidden_tuning:
        if bad in txt:
            errors.append(f"Contains forbidden mid-competition strategy tuning phrase: '{bad}'")

    return len(errors) == 0, errors


def _call_gemini_for_report(user_prompt: str, api_key: str) -> Tuple[Optional[str], Optional[str]]:
    """Call Gemini Flash with temperature=0.15 to draft the GFM daily report."""
    body = json.dumps(
        {
            "systemInstruction": {"parts": [{"text": DAILY_REPORT_SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.15,
                "maxOutputTokens": 4096,
            },
        }
    ).encode("utf-8")

    for model_name in (PRIMARY_REPORT_MODEL, FALLBACK_REPORT_MODEL):
        url = f"{GEMINI_API_BASE_URL}/{model_name}:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            cands = data.get("candidates") or []
            if not cands:
                continue
            parts = (cands[0].get("content") or {}).get("parts") or []
            text = "".join(str(p.get("text", "")) for p in parts).strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:markdown|md)?\s*", "", text, flags=re.IGNORECASE)
                text = re.sub(r"\s*```$", "", text).strip()
            if text:
                return text, None
        except Exception as exc:
            err_msg = re.sub(r"key=[^&\s\"']+", "key=[REDACTED]", str(exc))
            logger.warning("Gemini daily report call failed on %s: %s", model_name, err_msg)
    return None, "Gemini daily report API call failed"


def build_user_prompt_from_payload(payload: Dict[str, Any]) -> str:
    """Populate the exact User Prompt Template with pre-computed Python JSON blocks."""
    meta = payload["metadata"]
    return (
        "아래 제공된 **AI Trading Arena**의 당일 확정 JSON 데이터를 바탕으로 일일 성과 및 차트 분석 마크다운 보고서를 작성하세요.\n"
        "System Prompt의 모든 데이터 무결성 규칙, 전략 수정 금지 원칙, 고정 GFM 형식을 엄격히 준수하세요.\n\n"
        "### 1. 메타데이터\n"
        f"- **거래일 (ET)**: {meta['date_et']}\n"
        f"- **대회 진행 일차**: Day {meta['trading_day']} / 10\n"
        f"- **최종일 여부 (`is_final_day`)**: {'true' if meta['is_final_day'] else 'false'}\n"
        f"- **당일 세션 유형**: {meta['session_type']}\n"
        f"- **실행된 30분봉 사이클 수**: {meta['cycles_run_today']} / 13\n"
        f"- **다음 거래일 일정**: {meta['next_trading_day_info']}\n\n"
        "### 2. 시장 데이터 및 보조지표 스냅샷 (JSON)\n"
        f"```json\n{json.dumps(payload.get('market_summary') or {}, ensure_ascii=False)}\n```\n\n"
        "### 3. 사전 계산된 리더보드 및 전략군 집계 (JSON)\n"
        f"```json\n{json.dumps({'metadata': meta, 'leaderboard': payload['leaderboard'], 'group_summary': payload['group_summary'], 'friction_top3': payload['friction_top3'], 'circuit_breaker_watch': payload['circuit_breaker_watch']}, ensure_ascii=False)}\n```\n\n"
        "### 4. 당일 체결, 리스크 거부 및 보유 포지션 (JSON)\n"
        f"```json\n{json.dumps({'today_trades': payload['today_trades'][:35], 'today_rejections': payload['today_rejections'][:20], 'open_positions': payload['open_positions'][:20]}, ensure_ascii=False)}\n```\n\n"
        "### 5. 14번 `claude_rmp` 텔레메트리 (JSON)\n"
        f"```json\n{json.dumps(payload['claude_rmp_telemetry'], ensure_ascii=False)}\n```\n\n"
        "### 6. 최종 10일차 통계 검정 (JSON, 1~9일차는 null)\n"
        f"```json\n{json.dumps(payload.get('final_10day_stats'), ensure_ascii=False)}\n```\n"
    )


def generate_and_save_daily_report(
    agent_states: List[AgentLedgerState],
    competition_state: Dict[str, Any],
    trading_date_et: str,
    trading_day_number: int,
    market_summary: Optional[Dict[str, Any]] = None,
    state_dir: Path = STATE_DIR,
    reports_dir: Path = REPORTS_DIR,
    use_llm: bool = True,
    cycles_run_today: Optional[int] = None,
) -> Dict[str, Any]:
    """Build payload, generate verified GFM report (Gemini Flash + deterministic fallback), and save to disk."""
    # Load cached market summary & Agent 14 telemetry if not passed directly
    if market_summary is None:
        ms_path = state_dir / "last_market_summary.json"
        if ms_path.exists():
            try:
                market_summary = json.loads(ms_path.read_text(encoding="utf-8"))
            except Exception:
                market_summary = {}

    rmp_logs: List[Dict[str, Any]] = []
    rmp_logs_path = state_dir / "claude_rmp_cycle_logs.json"
    if rmp_logs_path.exists():
        try:
            rmp_logs = json.loads(rmp_logs_path.read_text(encoding="utf-8"))
        except Exception:
            rmp_logs = []

    rmp_state: Dict[str, Any] = {}
    rmp_state_path = state_dir / "claude_rmp_state.json"
    if rmp_state_path.exists():
        try:
            rmp_state = json.loads(rmp_state_path.read_text(encoding="utf-8"))
        except Exception:
            rmp_state = {}

    payload = build_report_payload(
        agent_states=agent_states,
        competition_state=competition_state,
        trading_date_et=trading_date_et,
        trading_day_number=trading_day_number,
        market_summary=market_summary,
        claude_rmp_logs=rmp_logs,
        claude_rmp_state=rmp_state,
        cycles_run_today=cycles_run_today,
    )
    is_final = bool(payload["metadata"]["is_final_day"])

    markdown_text: Optional[str] = None
    generator_mode = "deterministic-python"
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()

    post_check_ok = False
    if use_llm and api_key:
        user_prompt = build_user_prompt_from_payload(payload)
        llm_md, err = _call_gemini_for_report(user_prompt, api_key)
        if llm_md and not err:
            ok, post_errs = validate_report_markdown(llm_md, agent_states, is_final_day=is_final)
            if ok:
                markdown_text = llm_md + ("\n" if not llm_md.endswith("\n") else "")
                generator_mode = f"gemini-flash ({PRIMARY_REPORT_MODEL})"
                post_check_ok = True
            else:
                logger.warning("Gemini daily report failed post-check %s; falling back to deterministic renderer.", post_errs)

    if markdown_text is None:
        markdown_text = render_deterministic_markdown_report(payload)
        ok, post_errs = validate_report_markdown(markdown_text, agent_states, is_final_day=is_final)
        post_check_ok = ok
        if not ok:
            logger.error("Deterministic report post-check errors: %s", post_errs)

    daily_dir = reports_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    report_filename = f"{trading_date_et}_day{trading_day_number:02d}.md"
    report_path = daily_dir / report_filename
    report_path.write_text(markdown_text, encoding="utf-8")

    latest_path = reports_dir / "LATEST_REPORT.md"
    latest_path.write_text(markdown_text, encoding="utf-8")

    # Update reports log in state_dir
    log_path = state_dir / "daily_reports_log.json"
    entries: List[Dict[str, Any]] = []
    if log_path.exists():
        try:
            loaded = json.loads(log_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                entries = loaded
        except Exception:
            entries = []

    entry = {
        "trading_date_et": trading_date_et,
        "trading_day_number": trading_day_number,
        "is_final_day": is_final,
        "filename": report_filename,
        "relative_path": f"reports/daily/{report_filename}",
        "generator": generator_mode,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    entries = [e for e in entries if e.get("trading_date_et") != trading_date_et]
    entries.append(entry)
    entries.sort(key=lambda e: (e["trading_day_number"], e["trading_date_et"]))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "report_path": str(report_path),
        "latest_path": str(latest_path),
        "generator": generator_mode,
        "post_check_passed": post_check_ok,
        "entry": entry,
    }
