"""Cached point-in-time historical market prices, dividends, fundamentals, and SEC EPS data.

All computations strictly respect `as_of_date` so no future prices, dividends, or earnings
filings leak into historical pattern learning or out-of-sample backtests.
"""

from __future__ import annotations

import bisect
import http.cookiejar
import json
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arena.config import PROJECT_ROOT, UNIVERSE

logger = logging.getLogger(__name__)

DATA_CACHE_DIR = PROJECT_ROOT / "data_cache"
PRICES_CACHE_DIR = DATA_CACHE_DIR / "prices"
FUNDAMENTALS_CACHE_PATH = DATA_CACHE_DIR / "fundamentals_cache.json"
SEC_TICKERS_CACHE_PATH = DATA_CACHE_DIR / "sec_company_tickers.json"
SEC_EPS_CACHE_DIR = DATA_CACHE_DIR / "sec_eps"
CUSIP_MAP_CACHE_PATH = DATA_CACHE_DIR / "cusip_map.json"
UNIVERSE_FUNDAMENTALS_PATH = DATA_CACHE_DIR / "universe_fundamentals.json"

SEC_HEADERS = {"User-Agent": "AITradingArena research@ai-trading-arena.local"}
WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}

# Normalize Yahoo / GICS sector labels to consistent plain-English sector names
SECTOR_NORMALIZE = {
    "Technology": "Technology",
    "Information Technology": "Technology",
    "Financial Services": "Financials",
    "Financials": "Financials",
    "Financial": "Financials",
    "Consumer Defensive": "Consumer Staples",
    "Consumer Staples": "Consumer Staples",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Discretionary": "Consumer Discretionary",
    "Energy": "Energy",
    "Healthcare": "Health Care",
    "Health Care": "Health Care",
    "Industrials": "Industrials",
    "Communication Services": "Communication Services",
    "Basic Materials": "Materials",
    "Materials": "Materials",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
}

# Known fallback sector/size metadata for core universe ETFs & stocks when offline
ETF_SECTOR_MAP = {
    "XLK": "Technology",
    "SMH": "Technology",
    "QQQ": "Technology",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLE": "Energy",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLI": "Industrials",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
    "SPY": "Broad Market ETF",
    "DIA": "Broad Market ETF",
    "IWM": "Broad Market ETF",
    "TLT": "Fixed Income ETF",
    "GLD": "Commodity ETF",
}


def normalize_sector(raw_sector: Optional[str], ticker: str = "") -> str:
    if ticker in ETF_SECTOR_MAP:
        return ETF_SECTOR_MAP[ticker]
    if not raw_sector:
        return "Unknown"
    return SECTOR_NORMALIZE.get(raw_sector.strip(), raw_sector.strip())


def classify_market_cap(market_cap_usd: Optional[float]) -> str:
    """Classify company size into standard equity market-cap buckets."""
    if market_cap_usd is None or market_cap_usd <= 0:
        return "Unknown"
    if market_cap_usd >= 200_000_000_000:
        return "Mega-Cap ($200B+)"
    if market_cap_usd >= 10_000_000_000:
        return "Large-Cap ($10B-$200B)"
    if market_cap_usd >= 2_000_000_000:
        return "Mid-Cap ($2B-$10B)"
    return "Small-Cap (<$2B)"


def to_yahoo_symbol(ticker: str) -> str:
    """Convert tickers like BRK.B to BRK-B for Yahoo Finance endpoints."""
    return ticker.strip().upper().replace(".", "-")


class MarketHistoryCache:
    """Disk-cached daily price history, dividend events, Yahoo fundamentals, and SEC EPS."""

    def __init__(self, cache_dir: Path = DATA_CACHE_DIR, offline: bool = False) -> None:
        self.cache_dir = Path(cache_dir)
        self.prices_dir = self.cache_dir / "prices"
        self.sec_eps_dir = self.cache_dir / "sec_eps"
        self.fundamentals_path = self.cache_dir / "fundamentals_cache.json"
        self.sec_tickers_path = self.cache_dir / "sec_company_tickers.json"
        self.cusip_map_path = self.cache_dir / "cusip_map.json"
        self.offline = offline

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.prices_dir.mkdir(parents=True, exist_ok=True)
        self.sec_eps_dir.mkdir(parents=True, exist_ok=True)

        self._price_mem: Dict[str, Dict[str, Any]] = {}
        self._fundamentals: Dict[str, Dict[str, Any]] = self._load_json(self.fundamentals_path, {})
        self._cusip_map: Dict[str, Dict[str, Any]] = self._load_json(self.cusip_map_path, {})
        self._sec_ticker_to_cik: Dict[str, str] = {}
        self._yahoo_opener: Optional[urllib.request.OpenerDirector] = None
        self._yahoo_crumb: str = ""

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return default
        return default

    @staticmethod
    def _save_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _init_yahoo_session(self) -> Tuple[Optional[urllib.request.OpenerDirector], str]:
        if self._yahoo_opener is not None and self._yahoo_crumb:
            return self._yahoo_opener, self._yahoo_crumb
        if self.offline:
            return None, ""
        try:
            cj = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            opener.addheaders = [("User-Agent", WEB_HEADERS["User-Agent"])]
            try:
                opener.open("https://fc.yahoo.com", timeout=10)
            except Exception:
                pass
            crumb = opener.open("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10).read().decode("utf-8").strip()
            self._yahoo_opener = opener
            self._yahoo_crumb = crumb
            return opener, crumb
        except Exception as exc:
            logger.warning("Yahoo crumb session init failed: %s", exc)
            return None, ""

    # ------------------------------------------------------------------
    # Daily Price & Dividend History (Yahoo Chart API, cached per ticker)
    # ------------------------------------------------------------------

    def get_price_history(self, ticker: str, range_str: str = "7y") -> Optional[Dict[str, Any]]:
        """Return cached daily OHLC + dividend series for `ticker`."""
        sym = ticker.strip().upper()
        if not sym:
            return None
        if sym in self._price_mem:
            return self._price_mem[sym]

        safe_name = sym.replace("/", "_").replace(".", "_")
        cache_path = self.prices_dir / f"{safe_name}.json"
        if cache_path.exists():
            data = self._load_json(cache_path, None)
            if isinstance(data, dict) and data.get("dates"):
                self._price_mem[sym] = data
                return data

        if self.offline:
            return None

        ysim = to_yahoo_symbol(sym)
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.parse.quote(ysim)}?range={range_str}&interval=1d&events=div"
        )
        try:
            req = urllib.request.Request(url, headers=WEB_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            results = (raw.get("chart") or {}).get("result") or []
            if not results:
                return None
            res = results[0]
            timestamps = res.get("timestamp") or []
            quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
            adj_arr = ((res.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []

            dates: List[str] = []
            clean_open: List[float] = []
            clean_high: List[float] = []
            clean_low: List[float] = []
            clean_close: List[float] = []
            clean_adj: List[float] = []
            clean_vol: List[float] = []

            for i, ts in enumerate(timestamps):
                c = closes[i] if i < len(closes) else None
                if c is None or c <= 0:
                    continue
                d_str = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")
                o = opens[i] if i < len(opens) and opens[i] else c
                h = highs[i] if i < len(highs) and highs[i] else max(o, c)
                lo = lows[i] if i < len(lows) and lows[i] else min(o, c)
                adj = adj_arr[i] if i < len(adj_arr) and adj_arr[i] else c
                v = volumes[i] if i < len(volumes) and volumes[i] else 0.0
                if dates and dates[-1] == d_str:
                    clean_open[-1] = round(float(o), 4)
                    clean_high[-1] = round(float(h), 4)
                    clean_low[-1] = round(float(lo), 4)
                    clean_close[-1] = round(float(c), 4)
                    clean_adj[-1] = round(float(adj), 4)
                    clean_vol[-1] = float(v)
                else:
                    dates.append(d_str)
                    clean_open.append(round(float(o), 4))
                    clean_high.append(round(float(h), 4))
                    clean_low.append(round(float(lo), 4))
                    clean_close.append(round(float(c), 4))
                    clean_adj.append(round(float(adj), 4))
                    clean_vol.append(float(v))

            div_events = ((res.get("events") or {}).get("dividends") or {})
            dividends: List[Dict[str, Any]] = []
            for ev in div_events.values():
                d_ts = ev.get("date")
                amt = ev.get("amount")
                if d_ts is not None and amt is not None:
                    dividends.append(
                        {
                            "date": datetime.fromtimestamp(int(d_ts), timezone.utc).strftime("%Y-%m-%d"),
                            "amount": round(float(amt), 6),
                        }
                    )
            dividends.sort(key=lambda x: x["date"])

            if not dates:
                return None

            payload = {
                "ticker": sym,
                "dates": dates,
                "open": clean_open,
                "high": clean_high,
                "low": clean_low,
                "close": clean_close,
                "adjclose": clean_adj,
                "volume": clean_vol,
                "dividends": dividends,
            }
            self._save_json(cache_path, payload)
            self._price_mem[sym] = payload
            return payload
        except Exception as exc:
            logger.debug("Price history fetch failed for %s: %s", sym, exc)
            return None

    # ------------------------------------------------------------------
    # Point-in-Time Price & Technical Helpers (Strictly <= as_of_date)
    # ------------------------------------------------------------------

    def _index_on_or_before(self, hist: Dict[str, Any], as_of_date: str) -> int:
        dates = hist.get("dates") or []
        idx = bisect.bisect_right(dates, as_of_date) - 1
        return idx

    def get_close_on_or_before(self, ticker: str, as_of_date: str, use_adj: bool = False) -> Optional[float]:
        hist = self.get_price_history(ticker)
        if not hist:
            return None
        idx = self._index_on_or_before(hist, as_of_date)
        if idx < 0:
            return None
        arr = hist["adjclose"] if use_adj else hist["close"]
        return float(arr[idx])

    def get_open_on_or_after(self, ticker: str, after_date: str) -> Optional[Tuple[str, float]]:
        """Return (trade_date, open_price) strictly after `after_date` for zero look-ahead backtest fills."""
        hist = self.get_price_history(ticker)
        if not hist:
            return None
        dates = hist["dates"]
        idx = bisect.bisect_right(dates, after_date)
        if idx >= len(dates):
            return None
        return dates[idx], float(hist["open"][idx])

    def get_point_in_time_price_metrics(self, ticker: str, as_of_date: str) -> Optional[Dict[str, Any]]:
        """Compute strictly backward-looking metrics available on `as_of_date`:
        - close price
        - distance from 52-week (252 trading day) high in % (negative = below high)
        - 3-month (63 trading day) price trend in %
        - 30-calendar-day (21 trading day) price move in %
        - trailing 12-month dividend yield in %
        - 20-day realized annualized volatility in %
        """
        hist = self.get_price_history(ticker)
        if not hist:
            return None
        idx = self._index_on_or_before(hist, as_of_date)
        if idx < 21:
            return None

        closes = hist["close"]
        highs = hist["high"]
        adj = hist["adjclose"]
        dates = hist["dates"]

        px = float(closes[idx])
        if px <= 0:
            return None

        # 52-week high strictly over [max(0, idx-251) .. idx]
        start_52w = max(0, idx - 251)
        high_52w = max(float(h) for h in highs[start_52w : idx + 1])
        dist_from_52w_high_pct = ((px - high_52w) / high_52w) * 100.0 if high_52w > 0 else 0.0

        # 3-month (~63 trading days) total-return trend
        idx_3m = max(0, idx - 63)
        trend_3m_pct = ((float(adj[idx]) - float(adj[idx_3m])) / float(adj[idx_3m])) * 100.0 if adj[idx_3m] > 0 else 0.0

        # 30-calendar-day (~21 trading days) price move
        as_of_dt = date.fromisoformat(dates[idx])
        target_30d = (as_of_dt - timedelta(days=30)).isoformat()
        idx_30d = bisect.bisect_right(dates, target_30d) - 1
        if idx_30d < 0:
            idx_30d = max(0, idx - 21)
        move_30d_pct = ((float(adj[idx]) - float(adj[idx_30d])) / float(adj[idx_30d])) * 100.0 if adj[idx_30d] > 0 else 0.0

        # Trailing 12-month dividends paid strictly on or before as_of_date
        cutoff_1y = (as_of_dt - timedelta(days=365)).isoformat()
        div_sum = 0.0
        for ev in hist.get("dividends") or []:
            if cutoff_1y < ev["date"] <= dates[idx]:
                div_sum += float(ev["amount"])
        dividend_yield_pct = (div_sum / px) * 100.0 if px > 0 else 0.0

        # 20-day volatility & SMA50 for stability checks
        rets = [
            (closes[j] - closes[j - 1]) / closes[j - 1]
            for j in range(max(1, idx - 19), idx + 1)
            if closes[j - 1] > 0
        ]
        if len(rets) >= 2:
            m = sum(rets) / len(rets)
            vol_20d_ann = math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets)) * math.sqrt(252.0) * 100.0
        else:
            vol_20d_ann = 25.0

        return {
            "ticker": ticker.upper(),
            "as_of_date": dates[idx],
            "close": round(px, 4),
            "high_52w": round(high_52w, 4),
            "dist_from_52w_high_pct": round(dist_from_52w_high_pct, 2),
            "trend_3m_pct": round(trend_3m_pct, 2),
            "move_30d_pct": round(move_30d_pct, 2),
            "dividend_yield_pct": round(dividend_yield_pct, 2),
            "pays_dividend": dividend_yield_pct > 0.05,
            "vol_20d_ann_pct": round(vol_20d_ann, 2),
        }

    # ------------------------------------------------------------------
    # Yahoo Fundamentals (Sector, Market Cap, Current P/E, Dividend Yield)
    # ------------------------------------------------------------------

    def get_fundamentals(self, ticker: str, save: bool = True) -> Dict[str, Any]:
        sym = ticker.strip().upper()
        if sym in self._fundamentals:
            return self._fundamentals[sym]

        if sym in ETF_SECTOR_MAP:
            rec = {
                "ticker": sym,
                "name": sym,
                "sector": ETF_SECTOR_MAP[sym],
                "industry": "ETF",
                "quote_type": "ETF",
                "market_cap": None,
                "size_bucket": "ETF",
                "trailing_pe": None,
                "forward_pe": None,
                "dividend_yield_pct": None,
                "shares_outstanding": None,
            }
            self._fundamentals[sym] = rec
            if save:
                self._save_json(self.fundamentals_path, self._fundamentals)
            return rec

        if self.offline:
            return {
                "ticker": sym,
                "name": sym,
                "sector": "Unknown",
                "industry": "Unknown",
                "quote_type": "EQUITY",
                "market_cap": None,
                "size_bucket": "Unknown",
                "trailing_pe": None,
                "forward_pe": None,
                "dividend_yield_pct": None,
                "shares_outstanding": None,
            }

        opener, crumb = self._init_yahoo_session()
        if opener is None or not crumb:
            return {"ticker": sym, "sector": "Unknown", "size_bucket": "Unknown", "market_cap": None}

        ysim = to_yahoo_symbol(sym)
        url = (
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/"
            f"{urllib.parse.quote(ysim)}"
            f"?modules=assetProfile,summaryDetail,defaultKeyStatistics,price"
            f"&crumb={urllib.parse.quote(crumb)}"
        )
        try:
            raw = json.loads(opener.open(url, timeout=15).read().decode("utf-8"))
            res_list = (raw.get("quoteSummary") or {}).get("result") or []
            if not res_list:
                raise ValueError("empty quoteSummary")
            res = res_list[0]
            profile = res.get("assetProfile") or {}
            summary = res.get("summaryDetail") or {}
            stats = res.get("defaultKeyStatistics") or {}
            price_mod = res.get("price") or {}

            mcap = (price_mod.get("marketCap") or {}).get("raw")
            pe = (summary.get("trailingPE") or {}).get("raw")
            fpe = (summary.get("forwardPE") or {}).get("raw")
            dy = (summary.get("dividendYield") or {}).get("raw")
            shares_out = (stats.get("sharesOutstanding") or {}).get("raw")
            qtype = price_mod.get("quoteType") or "EQUITY"
            sec = normalize_sector(profile.get("sector"), sym)

            rec = {
                "ticker": sym,
                "name": price_mod.get("shortName") or price_mod.get("longName") or sym,
                "sector": sec,
                "industry": profile.get("industry") or "Unknown",
                "quote_type": qtype,
                "market_cap": float(mcap) if mcap else None,
                "size_bucket": classify_market_cap(float(mcap)) if mcap else ("ETF" if qtype == "ETF" else "Unknown"),
                "trailing_pe": round(float(pe), 2) if pe and 0 < float(pe) < 500 else None,
                "forward_pe": round(float(fpe), 2) if fpe and 0 < float(fpe) < 500 else None,
                "dividend_yield_pct": round(float(dy) * 100.0, 2) if dy is not None else 0.0,
                "shares_outstanding": float(shares_out) if shares_out else None,
            }
            self._fundamentals[sym] = rec
            if save:
                self._save_json(self.fundamentals_path, self._fundamentals)
            return rec
        except Exception as exc:
            logger.debug("Fundamentals fetch failed for %s: %s", sym, exc)
            rec = {
                "ticker": sym,
                "name": sym,
                "sector": "Unknown",
                "industry": "Unknown",
                "quote_type": "EQUITY",
                "market_cap": None,
                "size_bucket": "Unknown",
                "trailing_pe": None,
                "forward_pe": None,
                "dividend_yield_pct": None,
                "shares_outstanding": None,
            }
            self._fundamentals[sym] = rec
            if save:
                self._save_json(self.fundamentals_path, self._fundamentals)
            return rec

    def get_historical_market_cap(self, ticker: str, as_of_date: str) -> Optional[float]:
        """Estimate point-in-time market cap on `as_of_date` via shares_outstanding * historical price."""
        fund = self.get_fundamentals(ticker)
        px = self.get_close_on_or_before(ticker, as_of_date)
        if px is None or px <= 0:
            return fund.get("market_cap")
        shares_out = fund.get("shares_outstanding")
        if shares_out and shares_out > 0:
            return float(shares_out) * px
        # Fallback ratio using current market cap & latest price
        mcap_now = fund.get("market_cap")
        hist = self.get_price_history(ticker)
        if mcap_now and hist and hist.get("close"):
            latest_px = float(hist["close"][-1])
            if latest_px > 0:
                return float(mcap_now) * (px / latest_px)
        return mcap_now

    # ------------------------------------------------------------------
    # SEC EDGAR Point-in-Time Diluted EPS (for No-Look-Ahead Historical P/E)
    # ------------------------------------------------------------------

    def _load_sec_ticker_map(self) -> Dict[str, str]:
        if self._sec_ticker_to_cik:
            return self._sec_ticker_to_cik
        raw = self._load_json(self.sec_tickers_path, None)
        if raw is None and not self.offline:
            try:
                req = urllib.request.Request("https://www.sec.gov/files/company_tickers.json", headers=SEC_HEADERS)
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                self._save_json(self.sec_tickers_path, raw)
            except Exception as exc:
                logger.warning("Failed to fetch SEC company_tickers.json: %s", exc)
                raw = {}
        mapping: Dict[str, str] = {}
        if isinstance(raw, dict):
            for entry in raw.values():
                t = str(entry.get("ticker", "")).upper().replace("-", ".")
                cik = str(entry.get("cik_str", "")).zfill(10)
                if t and cik:
                    mapping[t] = cik
                    mapping[t.replace(".", "-")] = cik
        self._sec_ticker_to_cik = mapping
        return mapping

    def get_sec_eps_history(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch and cache SEC XBRL annual/quarterly Diluted EPS facts with exact `filed` dates."""
        sym = ticker.strip().upper()
        safe = sym.replace("/", "_").replace(".", "_")
        cache_path = self.sec_eps_dir / f"{safe}.json"
        if cache_path.exists():
            return self._load_json(cache_path, [])
        if self.offline:
            return []

        cik_map = self._load_sec_ticker_map()
        cik = cik_map.get(sym) or cik_map.get(sym.replace(".", "-"))
        if not cik:
            self._save_json(cache_path, [])
            return []

        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        try:
            time.sleep(0.12)  # Polite SEC EDGAR rate limit (<10 req/sec)
            req = urllib.request.Request(url, headers=SEC_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            us_gaap = (data.get("facts") or {}).get("us-gaap") or {}
            eps_obj = us_gaap.get("EarningsPerShareDiluted") or us_gaap.get("EarningsPerShareBasic") or {}
            units = eps_obj.get("units") or {}
            items = units.get("USD/shares") or []

            records: List[Dict[str, Any]] = []
            for it in items:
                form = str(it.get("form", ""))
                if form not in ("10-K", "10-Q", "20-F"):
                    continue
                start = it.get("start")
                end = it.get("end")
                filed = it.get("filed")
                val = it.get("val")
                if not (start and end and filed and val is not None):
                    continue
                try:
                    span_days = (date.fromisoformat(end) - date.fromisoformat(start)).days
                except Exception:
                    continue
                records.append(
                    {
                        "start": start,
                        "end": end,
                        "filed": filed,
                        "span_days": span_days,
                        "val": float(val),
                        "form": form,
                    }
                )
            records.sort(key=lambda r: (r["filed"], r["end"]))
            self._save_json(cache_path, records)
            return records
        except Exception as exc:
            logger.debug("SEC companyfacts EPS fetch failed for %s: %s", sym, exc)
            self._save_json(cache_path, [])
            return []

    def get_point_in_time_pe(self, ticker: str, as_of_date: str) -> Optional[float]:
        """Compute strictly no-look-ahead P/E ratio using only SEC filings with `filed <= as_of_date`."""
        px = self.get_close_on_or_before(ticker, as_of_date)
        if px is None or px <= 0:
            return None

        records = self.get_sec_eps_history(ticker)
        public_recs = [r for r in records if r["filed"] <= as_of_date]
        if not public_recs:
            return None

        # Prefer most recent annual (~330-390 day) 10-K EPS, or sum of 4 most recent distinct quarterly (~75-105 day) EPS
        quarterly: Dict[str, float] = {}
        annuals: List[Dict[str, Any]] = []
        for r in public_recs:
            if 70 <= r["span_days"] <= 110:
                quarterly[r["end"]] = r["val"]
            elif 330 <= r["span_days"] <= 390:
                annuals.append(r)

        ttm_eps: Optional[float] = None
        if len(quarterly) >= 4:
            recent_q_ends = sorted(quarterly.keys())[-4:]
            # Verify the 4 quarters span roughly 1 year (within 420 days)
            try:
                span = (date.fromisoformat(recent_q_ends[-1]) - date.fromisoformat(recent_q_ends[0])).days
                if 240 <= span <= 420:
                    ttm_eps = sum(quarterly[k] for k in recent_q_ends)
            except Exception:
                pass

        if ttm_eps is None and annuals:
            annuals.sort(key=lambda x: (x["end"], x["filed"]))
            ttm_eps = annuals[-1]["val"]

        if ttm_eps is None or ttm_eps <= 0:
            return None

        pe = px / ttm_eps
        if 1.0 <= pe <= 250.0:
            return round(pe, 2)
        return None

    # ------------------------------------------------------------------
    # CUSIP -> Ticker Resolver (OpenFIGI + Yahoo Search fallback, cached)
    # ------------------------------------------------------------------

    def resolve_cusip_to_ticker(self, cusip: str, issuer_name: str = "") -> Optional[str]:
        clean_cusip = cusip.strip().upper()
        if clean_cusip in self._cusip_map:
            return self._cusip_map[clean_cusip].get("ticker")

        if self.offline:
            return None

        ticker: Optional[str] = None
        # 1. Try OpenFIGI public API (single or batch)
        try:
            body = json.dumps([{"idType": "ID_CUSIP", "idValue": clean_cusip, "exchCode": "US"}]).encode("utf-8")
            req = urllib.request.Request(
                "https://api.openfigi.com/v3/mapping",
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": WEB_HEADERS["User-Agent"]},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                res = json.loads(resp.read().decode("utf-8"))
            if isinstance(res, list) and res and res[0].get("data"):
                for item in res[0]["data"]:
                    if item.get("securityType") in ("Common Stock", "ETP", "ADR", "REIT", "NY Reg Shrs"):
                        t = item.get("ticker")
                        if t:
                            ticker = str(t).strip().upper().replace("/", ".")
                            break
                if not ticker:
                    t = res[0]["data"][0].get("ticker")
                    if t:
                        ticker = str(t).strip().upper().replace("/", ".")
            time.sleep(0.25)
        except Exception:
            pass

        # 2. Fallback: Yahoo Finance search by issuer name
        if not ticker and issuer_name:
            try:
                q = urllib.parse.quote(issuer_name.strip())
                url = f"https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=5&newsCount=0"
                req = urllib.request.Request(url, headers=WEB_HEADERS)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                for qt in res.get("quotes") or []:
                    if qt.get("quoteType") == "EQUITY" and qt.get("exchange") in ("NYQ", "NMS", "NGM", "ASE", "PCX"):
                        t = qt.get("symbol")
                        if t:
                            ticker = str(t).strip().upper().replace("-", ".")
                            break
            except Exception:
                pass

        self._cusip_map[clean_cusip] = {"ticker": ticker, "issuer_name": issuer_name}
        self._save_json(self.cusip_map_path, self._cusip_map)
        return ticker

    def bulk_resolve_cusips(self, cusip_to_name: Dict[str, str]) -> Dict[str, Optional[str]]:
        """Resolve a dict of {cusip: issuer_name} in batches via OpenFIGI + Yahoo fallback."""
        missing = [c for c in cusip_to_name if c not in self._cusip_map]
        if missing and not self.offline:
            # OpenFIGI allows up to 10 jobs per unauthenticated request
            for i in range(0, len(missing), 10):
                batch = missing[i : i + 10]
                jobs = [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in batch]
                try:
                    req = urllib.request.Request(
                        "https://api.openfigi.com/v3/mapping",
                        data=json.dumps(jobs).encode("utf-8"),
                        headers={"Content-Type": "application/json", "User-Agent": WEB_HEADERS["User-Agent"]},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        results = json.loads(resp.read().decode("utf-8"))
                    for c, item_res in zip(batch, results):
                        t = None
                        for d in item_res.get("data") or []:
                            if d.get("securityType") in ("Common Stock", "ETP", "ADR", "REIT", "NY Reg Shrs"):
                                t = str(d.get("ticker", "")).strip().upper().replace("/", ".")
                                break
                        if not t and (item_res.get("data") or []):
                            t = str(item_res["data"][0].get("ticker", "")).strip().upper().replace("/", ".")
                        if t:
                            self._cusip_map[c] = {"ticker": t, "issuer_name": cusip_to_name[c]}
                    time.sleep(0.45)
                except Exception as exc:
                    logger.debug("OpenFIGI batch failed: %s", exc)
                    break

            # Fallback per-item for any still unresolved
            for c in missing:
                if c not in self._cusip_map or not self._cusip_map[c].get("ticker"):
                    self.resolve_cusip_to_ticker(c, cusip_to_name[c])
            self._save_json(self.cusip_map_path, self._cusip_map)

        return {c: (self._cusip_map.get(c) or {}).get("ticker") for c in cusip_to_name}

    # ------------------------------------------------------------------
    # Refresh Universe Fundamentals & Daily History Cache (for Live Agents)
    # ------------------------------------------------------------------

    def build_universe_snapshot_cache(self, as_of_date: str) -> Dict[str, Dict[str, Any]]:
        """Cache daily prices, fundamentals, and point-in-time metrics for all UNIVERSE stocks."""
        out: Dict[str, Dict[str, Any]] = {}
        for sym in UNIVERSE:
            fund = self.get_fundamentals(sym, save=False)
            pm = self.get_point_in_time_price_metrics(sym, as_of_date) or {}
            pe = self.get_point_in_time_pe(sym, as_of_date) or fund.get("trailing_pe")
            mcap = self.get_historical_market_cap(sym, as_of_date) or fund.get("market_cap")
            div_y = pm.get("dividend_yield_pct")
            if div_y is None:
                div_y = fund.get("dividend_yield_pct") or 0.0
            out[sym] = {
                "ticker": sym,
                "as_of_date": as_of_date,
                "sector": fund.get("sector", "Unknown"),
                "quote_type": fund.get("quote_type", "EQUITY"),
                "market_cap": round(float(mcap), 2) if mcap else None,
                "size_bucket": classify_market_cap(float(mcap)) if mcap else fund.get("size_bucket", "Unknown"),
                "pe_ratio": round(float(pe), 2) if pe else None,
                "dividend_yield_pct": round(float(div_y), 2),
                "pays_dividend": float(div_y) > 0.05,
                "dist_from_52w_high_pct": pm.get("dist_from_52w_high_pct"),
                "trend_3m_pct": pm.get("trend_3m_pct"),
                "move_30d_pct": pm.get("move_30d_pct"),
                "high_52w": pm.get("high_52w"),
                "close": pm.get("close"),
            }
        self._save_json(self.fundamentals_path, self._fundamentals)
        self._save_json(UNIVERSE_FUNDAMENTALS_PATH, out)
        return out
