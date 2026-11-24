"""Alpaca Paper API & Market Data client using only Python standard library.

Enforces two hard safety invariants:
1. Only https://paper-api.alpaca.markets is ever permitted for trading/clock/calendar endpoints.
2. No order placement endpoint (POST /v2/orders) is implemented anywhere in this client.
   All agent trades are executed exclusively in the local virtual ledger.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from arena.config import (
    ALPACA_DATA_BASE_URL,
    ALPACA_PAPER_BASE_URL,
    DEFAULT_SPREAD_BPS,
    PROJECT_ROOT,
    UNIVERSE,
    assert_paper_trading_only,
)
from arena.models import Bar, MarketSnapshot, NewsHeadline, Quote, TickerIndicators


# ---------------------------------------------------------------------------
# Timezone & ISO-8601 Helpers (Pure stdlib with Windows fallback for tzdata)
# ---------------------------------------------------------------------------

def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Return the date of the n-th weekday (0=Mon..6=Sun) in a given month."""
    first_day = date(year, month, 1)
    offset = (weekday - first_day.weekday()) % 7
    return first_day + timedelta(days=offset + 7 * (n - 1))


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of weekday (0=Mon..6=Sun) in a given month."""
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _is_us_eastern_dst(dt_utc: datetime) -> bool:
    """Determine if a UTC datetime falls in US Eastern Daylight Time (EDT, UTC-4).

    US DST starts 2nd Sunday in March at 02:00 EST (07:00 UTC)
    and ends 1st Sunday in November at 02:00 EDT (06:00 UTC).
    """
    year = dt_utc.year
    dst_start_date = _nth_weekday_of_month(year, 3, 6, 2)  # 2nd Sunday in March
    dst_end_date = _nth_weekday_of_month(year, 11, 6, 1)   # 1st Sunday in November
    dst_start_utc = datetime(
        year, 3, dst_start_date.day, 7, 0, 0, tzinfo=timezone.utc
    )
    dst_end_utc = datetime(
        year, 11, dst_end_date.day, 6, 0, 0, tzinfo=timezone.utc
    )
    return dst_start_utc <= dt_utc < dst_end_utc


try:
    _ET_ZONEINFO = ZoneInfo("America/New_York") if ZoneInfo is not None else None
except Exception:
    _ET_ZONEINFO = None


def get_et_tz(dt_utc: Optional[datetime] = None) -> timezone:
    """Return America/New_York tzinfo, falling back to exact US DST rules if Windows lacks tzdata."""
    if _ET_ZONEINFO is not None:
        return _ET_ZONEINFO  # type: ignore[return-value]
    ref = dt_utc or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    else:
        ref = ref.astimezone(timezone.utc)
    offset_hours = -4 if _is_us_eastern_dst(ref) else -5
    return timezone(timedelta(hours=offset_hours), name="EDT" if offset_hours == -4 else "EST")


def parse_iso_utc(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime."""
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    # Handle high-precision nanoseconds from Alpaca (e.g. .123456789+00:00)
    if "." in cleaned:
        plus_idx = cleaned.find("+", cleaned.find("."))
        minus_idx = cleaned.find("-", cleaned.find("."))
        tz_idx = plus_idx if plus_idx != -1 else minus_idx
        if tz_idx != -1:
            frac = cleaned[cleaned.find(".") + 1 : tz_idx]
            if len(frac) > 6:
                cleaned = cleaned[: cleaned.find(".") + 1] + frac[:6] + cleaned[tz_idx:]
        else:
            frac = cleaned[cleaned.find(".") + 1 :]
            if len(frac) > 6:
                cleaned = cleaned[: cleaned.find(".") + 1] + frac[:6]
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_iso_utc(dt: datetime) -> str:
    """Format a datetime as a canonical ISO-8601 UTC string ending with Z."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_et_datetime(ts_or_dt: str | datetime) -> datetime:
    """Convert an ISO-8601 timestamp string or UTC datetime to US Eastern time."""
    dt_utc = parse_iso_utc(ts_or_dt) if isinstance(ts_or_dt, str) else ts_or_dt.astimezone(timezone.utc)
    tz_et = get_et_tz(dt_utc)
    return dt_utc.astimezone(tz_et)


def et_wall_to_utc(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    """Convert an America/New_York wall-clock date/time into a timezone-aware UTC datetime."""
    if _ET_ZONEINFO is not None:
        local_dt = datetime(year, month, day, hour, minute, second, tzinfo=_ET_ZONEINFO)
        return local_dt.astimezone(timezone.utc)
    approx_utc = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc) + timedelta(hours=5)
    offset_hours = -4 if _is_us_eastern_dst(approx_utc) else -5
    tz_fixed = timezone(timedelta(hours=offset_hours))
    return datetime(year, month, day, hour, minute, second, tzinfo=tz_fixed).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# .env Loader (never prints or logs secret values)
# ---------------------------------------------------------------------------

def load_dotenv_if_present(dotenv_path: Optional[Path] = None) -> None:
    """Load key=value pairs from .env in PROJECT_ROOT into os.environ without printing any secrets."""
    target = dotenv_path or (PROJECT_ROOT / ".env")
    if not target.exists() or not target.is_file():
        return
    try:
        content = target.read_text(encoding="utf-8")
    except Exception:
        return
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if key and key not in os.environ and val:
            os.environ[key] = val


# ---------------------------------------------------------------------------
# US Market Calendar & Holiday / Early-Close Rules (Fallback & Validation)
# ---------------------------------------------------------------------------

def _observed_holiday(d: date) -> date:
    if d.weekday() == 5:  # Saturday -> observed Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday -> observed Monday
        return d + timedelta(days=1)
    return d


def get_us_market_holidays(year: int) -> set[date]:
    """Return standard NYSE/Nasdaq full-day holiday dates for a given year."""
    holidays: set[date] = set()
    # New Year's Day
    holidays.add(_observed_holiday(date(year, 1, 1)))
    # MLK Jr. Day (3rd Monday in January)
    holidays.add(_nth_weekday_of_month(year, 1, 0, 3))
    # Presidents' Day (3rd Monday in February)
    holidays.add(_nth_weekday_of_month(year, 2, 0, 3))
    # Memorial Day (last Monday in May)
    holidays.add(_last_weekday_of_month(year, 5, 0))
    # Juneteenth (June 19)
    holidays.add(_observed_holiday(date(year, 6, 19)))
    # Independence Day (July 4)
    holidays.add(_observed_holiday(date(year, 7, 4)))
    # Labor Day (1st Monday in September)
    holidays.add(_nth_weekday_of_month(year, 9, 0, 1))
    # Thanksgiving Day (4th Thursday in November)
    holidays.add(_nth_weekday_of_month(year, 11, 3, 4))
    # Christmas Day (December 25)
    holidays.add(_observed_holiday(date(year, 12, 25)))
    return holidays


def get_us_early_close_dates(year: int) -> set[date]:
    """Return NYSE 13:00 ET early-close dates (Black Friday, Christmas Eve, July 3)."""
    early_closes: set[date] = set()
    thanksgiving = _nth_weekday_of_month(year, 11, 3, 4)
    black_friday = thanksgiving + timedelta(days=1)
    early_closes.add(black_friday)
    xmas_eve = date(year, 12, 24)
    if xmas_eve.weekday() < 5 and xmas_eve not in get_us_market_holidays(year):
        early_closes.add(xmas_eve)
    july_3 = date(year, 7, 3)
    if july_3.weekday() < 5 and july_3 not in get_us_market_holidays(year):
        early_closes.add(july_3)
    return early_closes


def is_us_trading_day(d: date) -> bool:
    """Return True if date d is a valid NYSE/Nasdaq trading day."""
    if d.weekday() >= 5:
        return False
    return d not in get_us_market_holidays(d.year)


def get_session_hours_et(d: date) -> Tuple[str, str]:
    """Return (open_et, close_et) for a US trading date, e.g. ('09:30', '16:00') or ('09:30', '13:00')."""
    if d in get_us_early_close_dates(d.year):
        return ("09:30", "13:00")
    return ("09:30", "16:00")


def build_local_calendar(start_date: str, end_date: str) -> List[Dict[str, str]]:
    """Build a calendar list matching Alpaca's /v2/calendar schema for [start_date, end_date]."""
    cur = date.fromisoformat(start_date[:10])
    end = date.fromisoformat(end_date[:10])
    results: List[Dict[str, str]] = []
    while cur <= end:
        if is_us_trading_day(cur):
            open_et, close_et = get_session_hours_et(cur)
            results.append(
                {
                    "date": cur.isoformat(),
                    "open": open_et,
                    "close": close_et,
                    "session_open": open_et.replace(":", ""),
                    "session_close": close_et.replace(":", ""),
                }
            )
        cur += timedelta(days=1)
    return results


def compute_us_market_clock(now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Compute a fallback US market clock matching Alpaca's /v2/clock response."""
    now = now_utc or datetime.now(timezone.utc)
    et_now = to_et_datetime(now)
    cur_date = et_now.date()
    is_open = False

    if is_us_trading_day(cur_date):
        open_str, close_str = get_session_hours_et(cur_date)
        close_h, close_m = (int(x) for x in close_str.split(":"))
        open_dt_utc = et_wall_to_utc(cur_date.year, cur_date.month, cur_date.day, 9, 30)
        close_dt_utc = et_wall_to_utc(cur_date.year, cur_date.month, cur_date.day, close_h, close_m)
        if open_dt_utc <= now < close_dt_utc:
            is_open = True
            next_close_utc = close_dt_utc
            # Next open is next trading day at 09:30 ET
            probe = cur_date + timedelta(days=1)
            while not is_us_trading_day(probe):
                probe += timedelta(days=1)
            next_open_utc = et_wall_to_utc(probe.year, probe.month, probe.day, 9, 30)
            return {
                "timestamp": format_iso_utc(now),
                "is_open": True,
                "next_open": format_iso_utc(next_open_utc),
                "next_close": format_iso_utc(next_close_utc),
            }
        elif now < open_dt_utc:
            return {
                "timestamp": format_iso_utc(now),
                "is_open": False,
                "next_open": format_iso_utc(open_dt_utc),
                "next_close": format_iso_utc(close_dt_utc),
            }

    probe = cur_date + timedelta(days=1)
    while not is_us_trading_day(probe):
        probe += timedelta(days=1)
    open_str, close_str = get_session_hours_et(probe)
    close_h, close_m = (int(x) for x in close_str.split(":"))
    next_open_utc = et_wall_to_utc(probe.year, probe.month, probe.day, 9, 30)
    next_close_utc = et_wall_to_utc(probe.year, probe.month, probe.day, close_h, close_m)
    return {
        "timestamp": format_iso_utc(now),
        "is_open": is_open,
        "next_open": format_iso_utc(next_open_utc),
        "next_close": format_iso_utc(next_close_utc),
    }


# ---------------------------------------------------------------------------
# Technical Indicator & Regime Computation (Strictly No Look-Ahead)
# ---------------------------------------------------------------------------

def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std_pop(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    variance = sum((x - m) ** 2 for x in values) / len(values)
    return math.sqrt(max(0.0, variance))


def _compute_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < 2:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    window = deltas[-period:] if len(deltas) >= period else deltas
    gains = [d if d > 0 else 0.0 for d in window]
    losses = [-d if d < 0 else 0.0 for d in window]
    avg_gain = sum(gains) / len(window)
    avg_loss = sum(losses) / len(window)
    if avg_loss == 0.0 and avg_gain == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 95.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _compute_atr(bars: List[Bar], period: int = 14) -> float:
    if not bars:
        return 0.0
    if len(bars) == 1:
        return round(max(0.01, bars[0].high - bars[0].low), 4)
    trs: List[float] = []
    start_idx = max(1, len(bars) - period)
    for i in range(start_idx, len(bars)):
        cur = bars[i]
        prev_close = bars[i - 1].close
        tr = max(
            cur.high - cur.low,
            abs(cur.high - prev_close),
            abs(cur.low - prev_close),
        )
        trs.append(tr)
    atr_val = _mean(trs) if trs else (bars[-1].high - bars[-1].low)
    return round(max(0.01, atr_val), 4)


def compute_indicators_from_bars(
    ticker: str,
    bars: List[Bar],
    spy_bars: Optional[List[Bar]],
    trading_date_et: str,
) -> TickerIndicators:
    """Compute technical indicators for `ticker` from bars strictly <= decision_timestamp."""
    if not bars:
        return TickerIndicators(
            ticker=ticker,
            last_price=0.0,
            prev_close=0.0,
            change_pct_1d=0.0,
            change_pct_5d=0.0,
            sma_10=0.0,
            sma_20=0.0,
            sma_30=0.0,
            rsi_14=50.0,
            atr_14=0.01,
            bb_upper_20=0.0,
            bb_mid_20=0.0,
            bb_lower_20=0.0,
            high_20=0.0,
            low_20=0.0,
            opening_range_high=None,
            opening_range_low=None,
            volume_ratio=1.0,
            relative_strength_vs_spy=0.0,
        )

    closes = [b.close for b in bars]
    last_bar = bars[-1]
    last_price = round(last_bar.close, 4)

    # Identify bars belonging to today's ET trading session vs prior sessions
    today_bars: List[Bar] = []
    prior_day_bars: List[Bar] = []
    for b in bars:
        b_et_date = to_et_datetime(b.timestamp).strftime("%Y-%m-%d")
        if b_et_date == trading_date_et:
            today_bars.append(b)
        elif b_et_date < trading_date_et:
            prior_day_bars.append(b)

    if prior_day_bars:
        prev_close = round(prior_day_bars[-1].close, 4)
    elif len(bars) >= 14:
        prev_close = round(bars[-14].close, 4)
    else:
        prev_close = round(bars[0].open, 4)

    change_pct_1d = round(((last_price - prev_close) / prev_close) * 100.0, 2) if prev_close > 0 else 0.0

    ref_5d_idx = max(0, len(bars) - 65)
    ref_5d_price = bars[ref_5d_idx].close
    change_pct_5d = (
        round(((last_price - ref_5d_price) / ref_5d_price) * 100.0, 2)
        if ref_5d_price > 0
        else change_pct_1d
    )

    sma_10 = round(_mean(closes[-10:]), 4)
    sma_20 = round(_mean(closes[-20:]), 4)
    sma_30 = round(_mean(closes[-30:]), 4)

    rsi_14 = _compute_rsi(closes, 14)
    atr_14 = _compute_atr(bars, 14)

    std_20 = _std_pop(closes[-20:])
    bb_mid_20 = sma_20
    bb_upper_20 = round(bb_mid_20 + 2.0 * std_20, 4)
    bb_lower_20 = round(max(0.01, bb_mid_20 - 2.0 * std_20), 4)

    # Use the prior 20 bars for high_20 / low_20 so a current-bar breakout has last_price > high_20
    lookback_20_bars = bars[-21:-1] if len(bars) >= 2 else bars
    high_20 = round(max(b.high for b in lookback_20_bars), 4)
    low_20 = round(min(b.low for b in lookback_20_bars), 4)

    # Opening range from the first 30-min bar of trading_date_et
    if today_bars:
        first_session_bar = today_bars[0]
        opening_range_high: Optional[float] = round(first_session_bar.high, 4)
        opening_range_low: Optional[float] = round(first_session_bar.low, 4)
    else:
        opening_range_high = round(last_bar.high, 4)
        opening_range_low = round(last_bar.low, 4)

    vol_window = bars[-21:-1] if len(bars) >= 2 else bars
    avg_vol = _mean([b.volume for b in vol_window])
    volume_ratio = round(last_bar.volume / avg_vol, 2) if avg_vol > 0 else 1.0

    # Relative strength vs SPY (blending 1-day and 5-day outperformance in percentage points)
    rel_strength = 0.0
    if spy_bars and len(spy_bars) >= 2:
        spy_last = spy_bars[-1].close
        spy_prior = [
            b for b in spy_bars if to_et_datetime(b.timestamp).strftime("%Y-%m-%d") < trading_date_et
        ]
        spy_prev_close = spy_prior[-1].close if spy_prior else spy_bars[0].open
        spy_1d = ((spy_last - spy_prev_close) / spy_prev_close) * 100.0 if spy_prev_close > 0 else 0.0
        spy_5d_ref = spy_bars[max(0, len(spy_bars) - 65)].close
        spy_5d = ((spy_last - spy_5d_ref) / spy_5d_ref) * 100.0 if spy_5d_ref > 0 else spy_1d
        rel_strength = round(0.45 * (change_pct_1d - spy_1d) + 0.55 * (change_pct_5d - spy_5d), 2)

    return TickerIndicators(
        ticker=ticker,
        last_price=last_price,
        prev_close=prev_close,
        change_pct_1d=change_pct_1d,
        change_pct_5d=change_pct_5d,
        sma_10=sma_10,
        sma_20=sma_20,
        sma_30=sma_30,
        rsi_14=rsi_14,
        atr_14=atr_14,
        bb_upper_20=bb_upper_20,
        bb_mid_20=bb_mid_20,
        bb_lower_20=bb_lower_20,
        high_20=high_20,
        low_20=low_20,
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
        volume_ratio=volume_ratio,
        relative_strength_vs_spy=rel_strength,
    )


def compute_regime_summary(indicators: Dict[str, TickerIndicators]) -> str:
    """Synthesize a plain-English macro regime summary from SPY, QQQ, TLT, and GLD."""
    spy = indicators.get("SPY")
    qqq = indicators.get("QQQ")
    tlt = indicators.get("TLT")
    gld = indicators.get("GLD")

    if not spy:
        return "NEUTRAL REGIME: Benchmark SPY data unavailable."

    spy_trend = "UPTREND" if spy.sma_10 >= spy.sma_30 else "PULLBACK/DOWNTREND"
    qqq_1d = qqq.change_pct_1d if qqq else 0.0
    tlt_1d = tlt.change_pct_1d if tlt else 0.0
    gld_1d = gld.change_pct_1d if gld else 0.0

    if spy.sma_10 >= spy.sma_30 and spy.change_pct_1d >= -0.15 and qqq_1d >= spy.change_pct_1d - 0.25:
        regime_tag = "RISK-ON / BULLISH MOMENTUM"
    elif spy.change_pct_1d <= -0.60 or (spy.sma_10 < spy.sma_30 and gld_1d > 0.25):
        regime_tag = "RISK-OFF / DEFENSIVE"
    else:
        regime_tag = "MIXED / ROTATIONAL"

    rates_note = (
        f"Bonds (TLT {tlt_1d:+.2f}%) steady/rising (yields easing)"
        if tlt_1d >= 0
        else f"Bonds (TLT {tlt_1d:+.2f}%) lower (yields firming)"
    )
    gold_note = f"Gold (GLD {gld_1d:+.2f}%)"

    return (
        f"{regime_tag} — SPY {spy.last_price:.2f} ({spy.change_pct_1d:+.2f}% 1D, {spy.change_pct_5d:+.2f}% 5D, "
        f"{spy_trend}, RSI {spy.rsi_14:.1f}); QQQ ({qqq_1d:+.2f}% 1D); {rates_note}; {gold_note}."
    )


def compute_is_near_market_close(decision_timestamp: str, session_close_et: str = "16:00") -> bool:
    """Return True if decision_timestamp is within the final 35 minutes before session_close_et."""
    dt_et = to_et_datetime(decision_timestamp)
    close_h, close_m = (int(p) for p in session_close_et.split(":"))
    close_minutes = close_h * 60 + close_m
    cur_minutes = dt_et.hour * 60 + dt_et.minute
    remaining = close_minutes - cur_minutes
    return 0 <= remaining <= 35


# ---------------------------------------------------------------------------
# Alpaca Paper Data & Clock Client
# ---------------------------------------------------------------------------

class AlpacaDataClient:
    """Read-only Alpaca Paper & Market Data client using standard library HTTP."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper_base_url: Optional[str] = None,
        data_base_url: Optional[str] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        load_dotenv_if_present()
        self.paper_base_url = assert_paper_trading_only(paper_base_url)
        self.data_base_url = (
            data_base_url or os.environ.get("ALPACA_DATA_BASE_URL") or ALPACA_DATA_BASE_URL
        ).strip().rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get("ALPACA_API_KEY")
            or os.environ.get("APCA_API_KEY_ID")
            or ""
        ).strip()
        self.secret_key = (
            secret_key
            or os.environ.get("ALPACA_SECRET_KEY")
            or os.environ.get("APCA_API_SECRET_KEY")
            or ""
        ).strip()
        self.timeout_seconds = timeout_seconds

    def has_credentials(self) -> bool:
        """Return True if non-placeholder Alpaca API credentials are configured."""
        placeholders = {"", "your_paper_api_key_here", "your_paper_secret_key_here", "changeme"}
        return (
            self.api_key.lower() not in placeholders
            and self.secret_key.lower() not in placeholders
        )

    def _headers(self) -> Dict[str, str]:
        assert_paper_trading_only(self.paper_base_url)
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
            "User-Agent": "ai-trading-arena/1.0",
        }

    def _get_json(self, base_url: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any] | List[Any]:
        """Perform a GET request to Alpaca Paper or Data API after verifying paper-only safety."""
        assert_paper_trading_only(self.paper_base_url)
        if "/orders" in path.lower():
            raise RuntimeError("HARD RULE VIOLATION: Order endpoints are strictly forbidden.")
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean_params:
                url = f"{url}?{urllib.parse.urlencode(clean_params)}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)

    # ------------------------------------------------------------------
    # Clock & Calendar Endpoints
    # ------------------------------------------------------------------

    def get_clock(self) -> Dict[str, Any]:
        """Call GET https://paper-api.alpaca.markets/v2/clock (with local clock fallback if offline)."""
        assert_paper_trading_only(self.paper_base_url)
        if not self.has_credentials():
            return compute_us_market_clock()
        try:
            data = self._get_json(self.paper_base_url, "/v2/clock")
            if isinstance(data, dict) and "is_open" in data:
                return data
        except Exception:
            pass
        return compute_us_market_clock()

    def get_calendar(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Call GET https://paper-api.alpaca.markets/v2/calendar (handles Thanksgiving & Black Friday)."""
        assert_paper_trading_only(self.paper_base_url)
        if not self.has_credentials():
            return build_local_calendar(start_date, end_date)
        try:
            data = self._get_json(
                self.paper_base_url,
                "/v2/calendar",
                params={"start": start_date[:10], "end": end_date[:10]},
            )
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
        return build_local_calendar(start_date, end_date)

    # ------------------------------------------------------------------
    # Historical Bars, Quotes, and News (Strictly <= decision_timestamp)
    # ------------------------------------------------------------------

    def fetch_bars_up_to(
        self,
        tickers: List[str],
        decision_timestamp: str,
        lookback_days: int = 12,
        timeframe: str = "30Min",
    ) -> Dict[str, List[Bar]]:
        """Fetch 30-minute bars up to decision_timestamp, strictly filtering bar.timestamp <= decision_timestamp."""
        assert_paper_trading_only(self.paper_base_url)
        cutoff_dt = parse_iso_utc(decision_timestamp)
        cutoff_iso = format_iso_utc(cutoff_dt)
        start_dt = cutoff_dt - timedelta(days=lookback_days)
        start_iso = format_iso_utc(start_dt)

        bars_by_ticker: Dict[str, List[Bar]] = {t: [] for t in tickers}
        if not self.has_credentials():
            return bars_by_ticker

        # Batch tickers in chunks of 35 to keep query strings clean
        chunk_size = 35
        for i in range(0, len(tickers), chunk_size):
            batch = tickers[i : i + chunk_size]
            page_token: Optional[str] = None
            while True:
                params: Dict[str, Any] = {
                    "symbols": ",".join(batch),
                    "timeframe": timeframe,
                    "start": start_iso,
                    "end": cutoff_iso,
                    "limit": 10000,
                    "adjustment": "raw",
                    "feed": "iex",
                    "sort": "asc",
                }
                if page_token:
                    params["page_token"] = page_token
                payload = self._get_json(self.data_base_url, "/v2/stocks/bars", params=params)
                if not isinstance(payload, dict):
                    break
                raw_bars_map = payload.get("bars") or {}
                for sym, raw_list in raw_bars_map.items():
                    if sym not in bars_by_ticker or not isinstance(raw_list, list):
                        continue
                    for item in raw_list:
                        bar_ts = format_iso_utc(parse_iso_utc(str(item.get("t", ""))))
                        if parse_iso_utc(bar_ts) <= cutoff_dt:
                            bars_by_ticker[sym].append(
                                Bar(
                                    ticker=sym,
                                    timestamp=bar_ts,
                                    open=float(item.get("o", 0.0)),
                                    high=float(item.get("h", 0.0)),
                                    low=float(item.get("l", 0.0)),
                                    close=float(item.get("c", 0.0)),
                                    volume=float(item.get("v", 0.0)),
                                    vwap=float(item["vw"]) if item.get("vw") is not None else None,
                                )
                            )
                page_token = payload.get("next_page_token")
                if not page_token:
                    break

        for sym in bars_by_ticker:
            bars_by_ticker[sym].sort(key=lambda b: b.timestamp)
        return bars_by_ticker

    def fetch_quotes_up_to(
        self,
        tickers: List[str],
        decision_timestamp: str,
        bars_by_ticker: Optional[Dict[str, List[Bar]]] = None,
    ) -> Dict[str, Quote]:
        """Fetch latest quotes at or before decision_timestamp, synthesizing from last bar if unavailable."""
        assert_paper_trading_only(self.paper_base_url)
        cutoff_dt = parse_iso_utc(decision_timestamp)
        cutoff_iso = format_iso_utc(cutoff_dt)
        quotes: Dict[str, Quote] = {}

        if self.has_credentials():
            chunk_size = 40
            for i in range(0, len(tickers), chunk_size):
                batch = tickers[i : i + chunk_size]
                try:
                    payload = self._get_json(
                        self.data_base_url,
                        "/v2/stocks/quotes/latest",
                        params={"symbols": ",".join(batch), "feed": "iex"},
                    )
                    raw_quotes = payload.get("quotes", {}) if isinstance(payload, dict) else {}
                    for sym, q in raw_quotes.items():
                        q_ts_raw = q.get("t")
                        if not q_ts_raw:
                            continue
                        q_dt = parse_iso_utc(str(q_ts_raw))
                        if q_dt > cutoff_dt:
                            continue
                        bid = float(q.get("bp") or 0.0)
                        ask = float(q.get("ap") or 0.0)
                        if bid > 0 and ask >= bid:
                            mid = round((bid + ask) / 2.0, 4)
                            spread_bps = round(((ask - bid) / mid) * 10_000.0, 2) if mid > 0 else DEFAULT_SPREAD_BPS
                            # Cap anomalous wide IEX spreads to realistic liquid spread bounds
                            if spread_bps > 35.0:
                                half_spread = mid * (DEFAULT_SPREAD_BPS / 20_000.0)
                                bid = round(mid - half_spread, 4)
                                ask = round(mid + half_spread, 4)
                                spread_bps = DEFAULT_SPREAD_BPS
                            quotes[sym] = Quote(
                                ticker=sym,
                                timestamp=format_iso_utc(q_dt),
                                bid_price=round(bid, 4),
                                ask_price=round(ask, 4),
                                mid_price=mid,
                                spread_bps=spread_bps,
                            )
                except Exception:
                    pass

        # Fallback to last known bar at or before decision_timestamp for any missing quote
        if bars_by_ticker:
            for sym in tickers:
                if sym not in quotes and bars_by_ticker.get(sym):
                    last_bar = bars_by_ticker[sym][-1]
                    mid = round(last_bar.close, 4)
                    half_spread = mid * (DEFAULT_SPREAD_BPS / 20_000.0)
                    quotes[sym] = Quote(
                        ticker=sym,
                        timestamp=min(last_bar.timestamp, cutoff_iso),
                        bid_price=round(mid - half_spread, 4),
                        ask_price=round(mid + half_spread, 4),
                        mid_price=mid,
                        spread_bps=DEFAULT_SPREAD_BPS,
                    )
        return quotes

    def fetch_news_up_to(
        self,
        tickers: List[str],
        decision_timestamp: str,
        limit: int = 30,
    ) -> List[NewsHeadline]:
        """Fetch recent market news strictly created at or before decision_timestamp."""
        assert_paper_trading_only(self.paper_base_url)
        cutoff_dt = parse_iso_utc(decision_timestamp)
        cutoff_iso = format_iso_utc(cutoff_dt)
        start_iso = format_iso_utc(cutoff_dt - timedelta(days=3))
        headlines: List[NewsHeadline] = []

        if not self.has_credentials():
            return headlines

        try:
            payload = self._get_json(
                self.data_base_url,
                "/v1beta1/news",
                params={
                    "symbols": ",".join(tickers[:35]),
                    "start": start_iso,
                    "end": cutoff_iso,
                    "limit": limit,
                    "sort": "desc",
                },
            )
            items = payload.get("news", []) if isinstance(payload, dict) else []
            for item in items:
                created_raw = str(item.get("created_at") or "")
                if not created_raw:
                    continue
                created_dt = parse_iso_utc(created_raw)
                if created_dt <= cutoff_dt:
                    headlines.append(
                        NewsHeadline(
                            headline=str(item.get("headline") or "").strip(),
                            summary=str(item.get("summary") or "").strip()[:300],
                            source=str(item.get("source") or "Alpaca News"),
                            created_at=format_iso_utc(created_dt),
                            symbols=[str(s) for s in (item.get("symbols") or [])],
                            url=str(item.get("url") or ""),
                        )
                    )
        except Exception:
            pass
        return headlines

    # ------------------------------------------------------------------
    # Full MarketSnapshot & Next-Snapshot Fill Fetchers
    # ------------------------------------------------------------------

    def fetch_market_snapshot(
        self,
        decision_timestamp: str,
        trading_day_number: int,
        universe: Optional[List[str]] = None,
    ) -> MarketSnapshot:
        """Fetch a complete point-in-time MarketSnapshot strictly up to decision_timestamp."""
        assert_paper_trading_only(self.paper_base_url)
        tickers = list(universe) if universe is not None else list(UNIVERSE)
        cutoff_dt = parse_iso_utc(decision_timestamp)
        cutoff_iso = format_iso_utc(cutoff_dt)
        dt_et = to_et_datetime(cutoff_dt)
        trading_date_et = dt_et.strftime("%Y-%m-%d")

        # Determine session open/close for trading_date_et
        cal = self.get_calendar(trading_date_et, trading_date_et)
        if cal:
            session_open_et = str(cal[0].get("open", "09:30"))
            session_close_et = str(cal[0].get("close", "16:00"))
        else:
            session_open_et, session_close_et = get_session_hours_et(dt_et.date())

        bars_by_ticker = self.fetch_bars_up_to(tickers, cutoff_iso)
        # Double-check zero look-ahead invariant on all bars
        for sym, blist in bars_by_ticker.items():
            bars_by_ticker[sym] = [b for b in blist if parse_iso_utc(b.timestamp) <= cutoff_dt]

        quotes_by_ticker = self.fetch_quotes_up_to(tickers, cutoff_iso, bars_by_ticker=bars_by_ticker)
        news = [
            n
            for n in self.fetch_news_up_to(tickers, cutoff_iso)
            if parse_iso_utc(n.created_at) <= cutoff_dt
        ]

        spy_bars = bars_by_ticker.get("SPY", [])
        indicators: Dict[str, TickerIndicators] = {}
        for sym in tickers:
            indicators[sym] = compute_indicators_from_bars(
                ticker=sym,
                bars=bars_by_ticker.get(sym, []),
                spy_bars=spy_bars,
                trading_date_et=trading_date_et,
            )

        regime_summary = compute_regime_summary(indicators)
        is_near_close = compute_is_near_market_close(cutoff_iso, session_close_et=session_close_et)
        daily_bars_by_ticker = self.fetch_completed_daily_bars_before(tickers, trading_date_et)

        return MarketSnapshot(
            decision_timestamp=cutoff_iso,
            trading_date_et=trading_date_et,
            trading_day_number=trading_day_number,
            is_near_market_close=is_near_close,
            session_open_et=session_open_et,
            session_close_et=session_close_et,
            bars_by_ticker=bars_by_ticker,
            quotes_by_ticker=quotes_by_ticker,
            indicators=indicators,
            news=news,
            regime_summary=regime_summary,
            daily_bars_by_ticker=daily_bars_by_ticker,
        )

    def fetch_completed_daily_bars_before(
        self,
        tickers: List[str],
        trading_date_et: str,
        lookback_calendar_days: int = 140,
    ) -> Dict[str, List[Bar]]:
        """Fetch split-adjusted completed daily bars strictly BEFORE `trading_date_et` (min ~85-95 bars)."""
        assert_paper_trading_only(self.paper_base_url)
        daily_bars: Dict[str, List[Bar]] = {sym: [] for sym in tickers}
        if not self.has_credentials():
            return daily_bars

        target_dt = date.fromisoformat(trading_date_et)
        start_str = (target_dt - timedelta(days=lookback_calendar_days)).isoformat()
        # End strictly at yesterday's 23:59:59 UTC so today's partial daily bar can never leak
        end_str = f"{(target_dt - timedelta(days=1)).isoformat()}T23:59:59Z"

        batch_size = 30
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            page_token: Optional[str] = None
            while True:
                params: Dict[str, Any] = {
                    "symbols": ",".join(batch),
                    "timeframe": "1Day",
                    "start": f"{start_str}T00:00:00Z",
                    "end": end_str,
                    "adjustment": "split",
                    "feed": "iex",
                    "limit": 10000,
                }
                if page_token:
                    params["page_token"] = page_token
                try:
                    payload = self._get_json(self.data_base_url, "/v2/stocks/bars", params=params)
                except Exception:
                    break
                bars_map = payload.get("bars", {}) if isinstance(payload, dict) else {}
                for sym, raw_list in bars_map.items():
                    if sym not in daily_bars:
                        continue
                    for rb in raw_list:
                        ts_raw = str(rb.get("t") or "")
                        if not ts_raw:
                            continue
                        b_date = to_et_datetime(ts_raw).strftime("%Y-%m-%d")
                        if b_date >= trading_date_et:
                            continue  # Strictly completed bars BEFORE decision date
                        daily_bars[sym].append(
                            Bar(
                                ticker=sym,
                                timestamp=f"{b_date}T21:00:00Z",
                                open=float(rb["o"]),
                                high=float(rb["h"]),
                                low=float(rb["l"]),
                                close=float(rb["c"]),
                                volume=float(rb.get("v", 0.0)),
                                vwap=float(rb["vw"]) if rb.get("vw") is not None else None,
                            )
                        )
                page_token = payload.get("next_page_token") if isinstance(payload, dict) else None
                if not page_token:
                    break

        for sym in daily_bars:
            daily_bars[sym].sort(key=lambda b: b.timestamp)
        return daily_bars

    def fetch_next_fill_quotes_and_bars(
        self,
        tickers: List[str],
        after_decision_timestamp: str,
    ) -> Tuple[Dict[str, Quote], Dict[str, Bar]]:
        """Fetch next-snapshot quotes and bars strictly after `after_decision_timestamp` for live fills."""
        assert_paper_trading_only(self.paper_base_url)
        decision_dt = parse_iso_utc(after_decision_timestamp)
        now_dt = datetime.now(timezone.utc)
        if now_dt <= decision_dt:
            time.sleep(1.1)
            now_dt = datetime.now(timezone.utc)
        fill_dt = max(now_dt, decision_dt + timedelta(seconds=1))
        fill_iso = format_iso_utc(fill_dt)

        bars_map = self.fetch_bars_up_to(tickers, fill_iso, lookback_days=5)
        next_bars: Dict[str, Bar] = {}
        for sym in tickers:
            blist = bars_map.get(sym, [])
            if blist:
                last_b = blist[-1]
                next_bars[sym] = Bar(
                    ticker=sym,
                    timestamp=fill_iso,
                    open=last_b.open,
                    high=last_b.high,
                    low=last_b.low,
                    close=last_b.close,
                    volume=last_b.volume,
                    vwap=last_b.vwap,
                )

        quotes_map = self.fetch_quotes_up_to(tickers, fill_iso, bars_by_ticker=bars_map)
        next_quotes: Dict[str, Quote] = {}
        for sym, q in quotes_map.items():
            q_dt = parse_iso_utc(q.timestamp)
            guaranteed_ts = format_iso_utc(max(q_dt, fill_dt))
            next_quotes[sym] = Quote(
                ticker=sym,
                timestamp=guaranteed_ts,
                bid_price=q.bid_price,
                ask_price=q.ask_price,
                mid_price=q.mid_price,
                spread_bps=q.spread_bps,
            )

        return next_quotes, next_bars


# Alias for convenience
AlpacaClient = AlpacaDataClient
