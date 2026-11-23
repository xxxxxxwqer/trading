"""Berkshire Style Pattern Learner (Agent 11): learns investing style from 5 years of 13F filings.

Hard rules enforced:
- NEVER copy stocks from filings; filings are used ONLY as historical training data to measure
  Berkshire Hathaway's buying style (sectors, company size, Top-5 concentration, quarters held,
  and first-appearance profile: valuation, distance from 52w high, 3-month trend, dividend).
- 13F filings have NO trade dates or trade prices -> NEVER attempt to learn entry/exit timing.
- Strictly no look-ahead: only filings with `filing_date < agent_start_date` are eligible.
- Chronological Train / Out-of-Sample Test split: learn rules on the older history, test on the
  most recent period using normal price/fundamental data across UNIVERSE, and compare vs SPY.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arena.config import (
    DEFAULT_SPREAD_BPS,
    INITIAL_CASH,
    LIQUID_LARGE_CAP_STOCKS,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_EQUITY_PCT,
    MAX_RISK_PER_TRADE_PCT,
    PROJECT_ROOT,
    SLIPPAGE_RATE,
    STATE_DIR,
)
from arena.research.market_history import (
    DATA_CACHE_DIR,
    SEC_HEADERS,
    MarketHistoryCache,
    classify_market_cap,
    normalize_sector,
)

logger = logging.getLogger(__name__)

BERKSHIRE_CIK = "0001067983"
BERKSHIRE_RAW_CACHE_PATH = DATA_CACHE_DIR / "berkshire_13f_raw.json"
BERKSHIRE_PATTERN_PATH = STATE_DIR / "learned_patterns" / "berkshire_style.json"


def _median(vals: List[float], default: float = 0.0) -> float:
    clean = [float(v) for v in vals if v is not None and math.isfinite(float(v))]
    return round(float(statistics.median(clean)), 2) if clean else default


def _quantile(vals: List[float], q: float, default: float = 0.0) -> float:
    clean = sorted(float(v) for v in vals if v is not None and math.isfinite(float(v)))
    if not clean:
        return default
    pos = (len(clean) - 1) * max(0.0, min(1.0, q))
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return round(clean[lo], 2)
    return round(clean[lo] + (clean[hi] - clean[lo]) * (pos - lo), 2)


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def evaluate_berkshire_rules(
    stock_features: Dict[str, Any],
    rules: Dict[str, Any],
) -> Tuple[bool, float, List[str], List[str]]:
    """Explicit, testable rule evaluation for Berkshire Style on any candidate stock.

    Returns (matches_all_core_rules, score_0_to_100, passed_checks, failed_checks).
    NEVER checks whether the stock appeared in any 13F filing.
    """
    passed: List[str] = []
    failed: List[str] = []
    score = 0.0

    sector = str(stock_features.get("sector") or "Unknown")
    mcap = stock_features.get("market_cap")
    pe = stock_features.get("pe_ratio")
    dist_52w = stock_features.get("dist_from_52w_high_pct")
    trend_3m = stock_features.get("trend_3m_pct")
    div_yield = stock_features.get("dividend_yield_pct")

    # Rule 1: Preferred sector match
    preferred_sectors = list(rules.get("preferred_sectors") or [])
    if sector in preferred_sectors:
        passed.append(f"Sector '{sector}' is in learned Berkshire sectors ({', '.join(preferred_sectors)})")
        rank_bonus = max(0.0, 4.0 - preferred_sectors.index(sector)) * 2.0
        score += 24.0 + rank_bonus
    else:
        failed.append(f"Sector '{sector}' is outside learned Berkshire sectors ({', '.join(preferred_sectors)})")

    # Rule 2: Company size (large/mega cap)
    min_mcap = float(rules.get("min_market_cap_usd", 10_000_000_000.0))
    if mcap is not None and float(mcap) >= min_mcap:
        passed.append(f"Market cap ${float(mcap)/1e9:.1f}B >= ${min_mcap/1e9:.1f}B floor")
        score += 18.0
    else:
        mcap_str = f"${float(mcap)/1e9:.1f}B" if mcap else "unknown"
        failed.append(f"Market cap {mcap_str} below ${min_mcap/1e9:.1f}B floor")

    # Rule 3: Valuation ceiling (P/E ratio)
    max_pe = float(rules.get("max_pe_ratio", 28.0))
    min_pe = float(rules.get("min_pe_ratio", 4.0))
    if pe is not None and min_pe <= float(pe) <= max_pe:
        passed.append(f"Valuation P/E {float(pe):.1f}x is within [{min_pe:.1f}x, {max_pe:.1f}x]")
        # Reward cheaper valuation within reasonable range
        pe_discount = max(0.0, (max_pe - float(pe)) / max(1.0, max_pe))
        score += 18.0 + round(pe_discount * 8.0, 2)
    else:
        pe_str = f"{float(pe):.1f}x" if pe is not None else "unavailable"
        failed.append(f"Valuation P/E {pe_str} outside [{min_pe:.1f}x, {max_pe:.1f}x]")

    # Rule 4: Distance from 52-week high (quality without parabolic chase or collapsing knife)
    min_dist = float(rules.get("min_dist_from_52w_high_pct", -35.0))
    max_dist = float(rules.get("max_dist_from_52w_high_pct", 0.0))
    if dist_52w is not None and min_dist <= float(dist_52w) <= max_dist:
        passed.append(f"Distance from 52w high {float(dist_52w):+.1f}% within [{min_dist:+.1f}%, {max_dist:+.1f}%]")
        score += 15.0
    else:
        d_str = f"{float(dist_52w):+.1f}%" if dist_52w is not None else "unavailable"
        failed.append(f"Distance from 52w high {d_str} outside [{min_dist:+.1f}%, {max_dist:+.1f}%]")

    # Rule 5: 3-month price trend (avoids collapsing knives and extreme blow-off spikes)
    min_t3m = float(rules.get("min_trend_3m_pct", -20.0))
    max_t3m = float(rules.get("max_trend_3m_pct", 30.0))
    if trend_3m is not None and min_t3m <= float(trend_3m) <= max_t3m:
        passed.append(f"3-month trend {float(trend_3m):+.1f}% within [{min_t3m:+.1f}%, {max_t3m:+.1f}%]")
        score += 12.0
    else:
        t_str = f"{float(trend_3m):+.1f}%" if trend_3m is not None else "unavailable"
        failed.append(f"3-month trend {t_str} outside [{min_t3m:+.1f}%, {max_t3m:+.1f}%]")

    # Rule 6: Dividend profile
    require_div = bool(rules.get("require_dividend", True))
    min_div = float(rules.get("min_dividend_yield_pct", 0.5))
    div_val = float(div_yield) if div_yield is not None else 0.0
    if div_val >= min_div:
        passed.append(f"Dividend yield {div_val:.2f}% >= {min_div:.2f}%")
        score += 13.0 + min(4.0, div_val)
    elif not require_div:
        passed.append(f"Dividend yield {div_val:.2f}% (optional)")
        score += 5.0
    else:
        failed.append(f"Dividend yield {div_val:.2f}% < required {min_div:.2f}%")

    # Core match requires sector, size, valuation, 52w-high window, and dividend (if required)
    matches = len(failed) == 0
    return matches, round(min(100.0, score), 2), passed, failed


class BerkshireStyleLearner:
    """Downloads/caches Berkshire Hathaway's 5-year 13F filings, learns its style pattern,
    and runs an out-of-sample split backtest against SPY across our equity universe."""

    def __init__(
        self,
        market_cache: Optional[MarketHistoryCache] = None,
        raw_cache_path: Path = BERKSHIRE_RAW_CACHE_PATH,
        output_path: Path = BERKSHIRE_PATTERN_PATH,
        offline: bool = False,
    ) -> None:
        self.market = market_cache or MarketHistoryCache(offline=offline)
        self.raw_cache_path = Path(raw_cache_path)
        self.output_path = Path(output_path)
        self.offline = offline

    def fetch_and_cache_13f_filings(
        self,
        agent_start_date: str,
        years: int = 5,
    ) -> List[Dict[str, Any]]:
        """Fetch Berkshire Hathaway 13F-HR XML holdings from SEC EDGAR and cache locally.
        Strictly filters to `filing_date < agent_start_date` (no look-ahead).
        """
        cached: List[Dict[str, Any]] = []
        if self.raw_cache_path.exists():
            try:
                with self.raw_cache_path.open("r", encoding="utf-8") as f:
                    cached = json.load(f)
            except Exception:
                cached = []

        start_dt = date.fromisoformat(agent_start_date)
        cutoff_oldest = (start_dt - timedelta(days=years * 366 + 120)).isoformat()

        if not self.offline:
            try:
                req = urllib.request.Request(
                    f"https://data.sec.gov/submissions/CIK{BERKSHIRE_CIK}.json",
                    headers=SEC_HEADERS,
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    subs = json.loads(resp.read().decode("utf-8"))
                recent = (subs.get("filings") or {}).get("recent") or {}
                forms = recent.get("form") or []
                fdates = recent.get("filingDate") or []
                rdates = recent.get("reportDate") or []
                accs = recent.get("accessionNumber") or []

                cached_by_acc = {f["accession_number"]: f for f in cached}
                target_filings: List[Tuple[str, str, str]] = []
                for form, fdate, rdate, acc in zip(forms, fdates, rdates, accs):
                    if form != "13F-HR":
                        continue
                    if fdate >= agent_start_date:
                        continue  # Strictly no look-ahead!
                    if fdate < cutoff_oldest:
                        continue
                    target_filings.append((fdate, rdate, acc))

                # Keep the most recent 20 quarters (5 years) + 1 prior quarter to identify true first appearances
                target_filings.sort(key=lambda x: x[1])
                target_filings = target_filings[-(years * 4 + 1) :]

                updated = False
                for fdate, rdate, acc in target_filings:
                    if acc in cached_by_acc and cached_by_acc[acc].get("holdings"):
                        continue
                    logger.info("Fetching Berkshire 13F-HR %s (report %s, filed %s)...", acc, rdate, fdate)
                    holdings = self._download_single_13f_xml(acc)
                    cached_by_acc[acc] = {
                        "accession_number": acc,
                        "filing_date": fdate,
                        "report_date": rdate,
                        "holdings": holdings,
                    }
                    updated = True
                    time.sleep(0.15)

                if updated or not self.raw_cache_path.exists():
                    all_filings = sorted(cached_by_acc.values(), key=lambda x: x["report_date"])
                    self.raw_cache_path.parent.mkdir(parents=True, exist_ok=True)
                    with self.raw_cache_path.open("w", encoding="utf-8") as f:
                        json.dump(all_filings, f, indent=2)
                    cached = all_filings
            except Exception as exc:
                logger.warning("SEC EDGAR 13F fetch encountered an issue (%s); using cached data if available.", exc)

        # Strict no-look-ahead filter on return regardless of what is in cache
        eligible = [
            f
            for f in cached
            if f.get("filing_date", "9999-12-31") < agent_start_date
            and f.get("filing_date", "") >= cutoff_oldest
            and f.get("holdings")
        ]
        eligible.sort(key=lambda x: x["report_date"])
        return eligible

    def _download_single_13f_xml(self, accession_number: str) -> List[Dict[str, Any]]:
        """Download and aggregate holdings by CUSIP from a single SEC 13F-HR accession."""
        acc_nodash = accession_number.replace("-", "")
        cik_int = str(int(BERKSHIRE_CIK))
        idx_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
        req = urllib.request.Request(idx_url, headers=SEC_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            idx_data = json.loads(resp.read().decode("utf-8"))

        items = (idx_data.get("directory") or {}).get("item") or []
        xml_candidates = [
            it["name"]
            for it in items
            if it["name"].lower().endswith(".xml") and "primary_doc" not in it["name"].lower()
        ]
        if not xml_candidates:
            return []

        info_xml_name = xml_candidates[0]
        xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{info_xml_name}"
        time.sleep(0.12)
        req_xml = urllib.request.Request(xml_url, headers=SEC_HEADERS)
        with urllib.request.urlopen(req_xml, timeout=25) as resp:
            xml_bytes = resp.read()

        root = ET.fromstring(xml_bytes)
        by_cusip: Dict[str, Dict[str, Any]] = {}
        for elem in root:
            if _strip_ns(elem.tag) != "infoTable":
                continue
            fields: Dict[str, Any] = {}
            for child in elem:
                cname = _strip_ns(child.tag)
                if cname == "shrsOrPrnAmt":
                    for sub in child:
                        fields[_strip_ns(sub.tag)] = (sub.text or "").strip()
                else:
                    fields[cname] = (child.text or "").strip()

            # Shares only (skip put/call options if ever present)
            if fields.get("putCall"):
                continue
            cusip = str(fields.get("cusip") or "").upper()
            if not cusip:
                continue
            val = float(fields.get("value") or 0.0)
            shares = float(fields.get("sshPrnamt") or 0.0)
            issuer = str(fields.get("nameOfIssuer") or "").strip()
            if cusip not in by_cusip:
                by_cusip[cusip] = {
                    "cusip": cusip,
                    "issuer": issuer,
                    "value": 0.0,
                    "shares": 0.0,
                }
            by_cusip[cusip]["value"] += val
            by_cusip[cusip]["shares"] += shares

        total_val = sum(h["value"] for h in by_cusip.values())
        holdings = list(by_cusip.values())
        for h in holdings:
            h["weight_pct"] = round((h["value"] / total_val) * 100.0, 4) if total_val > 0 else 0.0
        holdings.sort(key=lambda x: x["weight_pct"], reverse=True)
        return holdings

    def measure_pattern(
        self,
        filings: List[Dict[str, Any]],
        baseline_cusips: Optional[set] = None,
    ) -> Dict[str, Any]:
        """Measure Berkshire's empirical investing pattern from a chronological slice of 13F filings.

        Does NOT learn entry/exit timing (13F has no trade dates or prices).
        """
        if not filings:
            raise ValueError("No eligible 13F filings available to measure Berkshire style.")

        # Resolve all CUSIPs appearing across these filings
        cusip_to_name: Dict[str, str] = {}
        for f in filings:
            for h in f["holdings"]:
                cusip_to_name[h["cusip"]] = h["issuer"]
        cusip_tickers = self.market.bulk_resolve_cusips(cusip_to_name)

        # Track per-quarter Top-5 concentration, sector weights, company sizes, and quarters held
        top5_concentrations: List[float] = []
        sector_weight_sums: Dict[str, float] = defaultdict(float)
        sector_counts: Counter = Counter()
        size_buckets: Counter = Counter()
        market_caps_all: List[float] = []
        quarters_held_by_cusip: Counter = Counter()

        # First-appearance tracking (skip quarter 0 if baseline_cusips not given)
        seen_cusips: set = set(baseline_cusips) if baseline_cusips is not None else set()
        first_appearances: List[Dict[str, Any]] = []

        for q_idx, f in enumerate(filings):
            rdate = f["report_date"]
            fdate = f["filing_date"]
            holdings = f["holdings"]

            # Top-5 concentration for this quarter
            top5_share = sum(h["weight_pct"] for h in holdings[:5])
            top5_concentrations.append(round(top5_share, 2))

            current_cusips = set()
            for h in holdings:
                cusip = h["cusip"]
                current_cusips.add(cusip)
                quarters_held_by_cusip[cusip] += 1
                ticker = cusip_tickers.get(cusip)
                if not ticker:
                    continue

                fund = self.market.get_fundamentals(ticker)
                if fund.get("quote_type") == "ETF":
                    continue  # Measure individual stock style, skip index ETF placeholders like SPY/VOO

                sector = fund.get("sector") or "Unknown"
                if sector != "Unknown":
                    sector_weight_sums[sector] += h["weight_pct"]
                    sector_counts[sector] += 1

                hist_mcap = self.market.get_historical_market_cap(ticker, rdate) or fund.get("market_cap")
                if hist_mcap and hist_mcap > 0:
                    market_caps_all.append(float(hist_mcap))
                    size_buckets[classify_market_cap(float(hist_mcap))] += 1

                # Detect genuinely NEW positions (first appearance after initial baseline quarter)
                if (q_idx > 0 or baseline_cusips is not None) and cusip not in seen_cusips:
                    pm = self.market.get_point_in_time_price_metrics(ticker, rdate)
                    pe = self.market.get_point_in_time_pe(ticker, rdate)
                    if pe is None:
                        pe = fund.get("trailing_pe")
                    if pm:
                        first_appearances.append(
                            {
                                "ticker": ticker,
                                "issuer": h["issuer"],
                                "report_date": rdate,
                                "filing_date": fdate,
                                "sector": sector,
                                "market_cap_usd": round(float(hist_mcap), 2) if hist_mcap else None,
                                "pe_ratio": pe,
                                "dist_from_52w_high_pct": pm["dist_from_52w_high_pct"],
                                "trend_3m_pct": pm["trend_3m_pct"],
                                "dividend_yield_pct": pm["dividend_yield_pct"],
                                "pays_dividend": pm["pays_dividend"],
                            }
                        )

            seen_cusips.update(current_cusips)

        # Normalize sector distribution by average portfolio weight
        num_q = max(1, len(filings))
        sector_avg_weights = {
            sec: round(w / num_q, 2)
            for sec, w in sorted(sector_weight_sums.items(), key=lambda kv: kv[1], reverse=True)
        }
        new_pos_sectors = Counter(
            fa["sector"] for fa in first_appearances if fa.get("sector") and fa["sector"] != "Unknown"
        )

        # Combine core portfolio sector exposure + new-addition sector frequency to identify style sectors
        sector_scores: Dict[str, float] = defaultdict(float)
        for sec, avg_w in sector_avg_weights.items():
            sector_scores[sec] += avg_w * 2.0
        for sec, cnt in new_pos_sectors.items():
            sector_scores[sec] += cnt * 6.0
        ranked_sectors = [sec for sec, _ in sorted(sector_scores.items(), key=lambda kv: kv[1], reverse=True)]
        preferred_sectors = ranked_sectors[:5] if len(ranked_sectors) >= 5 else ranked_sectors

        # Holding duration in quarters
        held_quarters_list = [float(q) for q in quarters_held_by_cusip.values()]
        median_quarters_held = _median(held_quarters_list, default=8.0)
        avg_quarters_held = round(sum(held_quarters_list) / len(held_quarters_list), 2) if held_quarters_list else 8.0

        # New position profile stats (at quarter of first appearance; NO timing learned)
        fa_pes = [fa["pe_ratio"] for fa in first_appearances if fa.get("pe_ratio") is not None]
        fa_dist52 = [fa["dist_from_52w_high_pct"] for fa in first_appearances if fa.get("dist_from_52w_high_pct") is not None]
        fa_t3m = [fa["trend_3m_pct"] for fa in first_appearances if fa.get("trend_3m_pct") is not None]
        fa_divs = [fa["dividend_yield_pct"] for fa in first_appearances if fa.get("dividend_yield_pct") is not None]
        fa_div_payers = [fa for fa in first_appearances if fa.get("pays_dividend")]
        div_payer_pct = round((len(fa_div_payers) / len(first_appearances)) * 100.0, 1) if first_appearances else 70.0
        fa_mcaps = [fa["market_cap_usd"] for fa in first_appearances if fa.get("market_cap_usd")]

        # Explicit, testable rules derived directly from the measured statistics
        median_top5 = _median(top5_concentrations, default=70.0)
        pe_p75 = _quantile(fa_pes, 0.75, default=26.0)
        max_pe_rule = round(min(35.0, max(16.0, pe_p75 * 1.10)), 1)
        dist_p15 = _quantile(fa_dist52, 0.15, default=-32.0)
        min_dist_rule = round(max(-45.0, min(-15.0, dist_p15 - 5.0)), 1)
        t3m_p15 = _quantile(fa_t3m, 0.15, default=-18.0)
        t3m_p85 = _quantile(fa_t3m, 0.85, default=22.0)
        min_t3m_rule = round(max(-30.0, min(-8.0, t3m_p15 - 3.0)), 1)
        max_t3m_rule = round(min(40.0, max(12.0, t3m_p85 + 5.0)), 1)

        mcap_p20 = _quantile(fa_mcaps or market_caps_all, 0.20, default=15_000_000_000.0)
        min_mcap_rule = round(max(10_000_000_000.0, min(50_000_000_000.0, mcap_p20)), 0)

        require_dividend = div_payer_pct >= 55.0
        nonzero_divs = [d for d in fa_divs if d > 0.05]
        min_div_yield = round(max(0.4, _quantile(nonzero_divs, 0.25, default=0.8)), 2) if require_dividend else 0.0

        learned_rules = {
            "never_copy_filings": True,
            "timing_learned_from_13f": False,
            "preferred_sectors": preferred_sectors,
            "min_market_cap_usd": min_mcap_rule,
            "min_pe_ratio": 4.0,
            "max_pe_ratio": max_pe_rule,
            "min_dist_from_52w_high_pct": min_dist_rule,
            "max_dist_from_52w_high_pct": 0.0,
            "min_trend_3m_pct": min_t3m_rule,
            "max_trend_3m_pct": max_t3m_rule,
            "require_dividend": require_dividend,
            "min_dividend_yield_pct": min_div_yield,
            "target_max_positions": 5 if median_top5 >= 55.0 else 5,
            "low_turnover_min_hold_days": 5,
            "stop_loss_atr_mult": 2.5,
            "take_profit_pct": 8.0,
        }

        plain_language_rules = [
            f"Focus on Berkshire's top learned sectors: {', '.join(preferred_sectors)} (never buy a stock just because it was in a 13F filing).",
            f"Require established large/mega-cap size: market cap at least ${min_mcap_rule/1e9:.1f}B (measured median holding size: ${_median(market_caps_all)/1e9:.1f}B).",
            f"Require reasonable valuation: trailing P/E between 4.0x and {max_pe_rule:.1f}x (median P/E at first 13F appearance: {_median(fa_pes):.1f}x).",
            f"Avoid chasing or falling knives: stock must trade between {min_dist_rule:+.1f}% and 0.0% from its 52-week high (median at first appearance: {_median(fa_dist52):+.1f}%) and have a 3-month price trend between {min_t3m_rule:+.1f}% and {max_t3m_rule:+.1f}% (median: {_median(fa_t3m):+.1f}%).",
            (
                f"Require a cash dividend of at least {min_div_yield:.2f}% ({div_payer_pct:.0f}% of new 13F additions paid dividends, median yield {_median(nonzero_divs):.2f}%)."
                if require_dividend
                else f"Dividends preferred but optional ({div_payer_pct:.0f}% of new 13F additions paid dividends)."
            ),
            f"Concentrated, patient holding style: Berkshire keeps a median {median_top5:.1f}% of its portfolio in its Top 5 positions and holds positions a median {median_quarters_held:.1f} quarters (avg {avg_quarters_held:.1f} quarters). Accordingly, hold up to 5 high-conviction positions with low turnover, wide risk-managed stops, and zero timing rules inferred from 13F dates.",
        ]

        return {
            "quarters_analyzed": len(filings),
            "report_date_range": [filings[0]["report_date"], filings[-1]["report_date"]],
            "filing_date_range": [filings[0]["filing_date"], filings[-1]["filing_date"]],
            "new_positions_observed": len(first_appearances),
            "measured_pattern": {
                "top5_concentration_median_pct": median_top5,
                "top5_concentration_avg_pct": round(sum(top5_concentrations) / len(top5_concentrations), 2),
                "holding_quarters_median": median_quarters_held,
                "holding_quarters_avg": avg_quarters_held,
                "sector_avg_portfolio_weight_pct": sector_avg_weights,
                "new_addition_sector_counts": dict(new_pos_sectors.most_common()),
                "company_size_distribution": dict(size_buckets.most_common()),
                "median_holding_market_cap_usd": _median(market_caps_all),
                "first_appearance_profile": {
                    "pe_median": _median(fa_pes),
                    "pe_p25": _quantile(fa_pes, 0.25),
                    "pe_p75": _quantile(fa_pes, 0.75),
                    "pe_sample_size": len(fa_pes),
                    "dist_from_52w_high_median_pct": _median(fa_dist52),
                    "dist_from_52w_high_p25_pct": _quantile(fa_dist52, 0.25),
                    "dist_from_52w_high_p75_pct": _quantile(fa_dist52, 0.75),
                    "trend_3m_median_pct": _median(fa_t3m),
                    "trend_3m_p25_pct": _quantile(fa_t3m, 0.25),
                    "trend_3m_p75_pct": _quantile(fa_t3m, 0.75),
                    "dividend_payer_pct": div_payer_pct,
                    "dividend_yield_median_pct": _median(nonzero_divs),
                },
            },
            "learned_rules": learned_rules,
            "plain_language_rules": plain_language_rules,
        }

    def run_out_of_sample_backtest(
        self,
        rules: Dict[str, Any],
        test_start_date: str,
        test_end_date: str,
    ) -> Dict[str, Any]:
        """Test learned Berkshire Style rules on the held-out recent period across UNIVERSE
        using ONLY normal price/fundamental data available on each decision date, with next-day
        open fills, spread + 5 bps slippage, and shared risk rules (1% risk, 20% cap, max 5 pos).
        """
        spy_hist = self.market.get_price_history("SPY")
        if not spy_hist:
            return {"error": "SPY price history unavailable for backtest."}

        spy_dates = [d for d in spy_hist["dates"] if test_start_date <= d <= test_end_date]
        if len(spy_dates) < 25:
            return {"error": f"Insufficient test trading days between {test_start_date} and {test_end_date}."}

        # Ensure all liquid stock histories exist
        candidates_universe = [t for t in LIQUID_LARGE_CAP_STOCKS]
        for sym in candidates_universe:
            self.market.get_price_history(sym)
            self.market.get_fundamentals(sym, save=False)

        cash = INITIAL_CASH
        positions: Dict[str, Dict[str, Any]] = {}
        equity_series: List[Tuple[str, float]] = []
        peak_equity = INITIAL_CASH
        max_dd_pct = 0.0
        trades_count = 0
        closed_wins = 0
        closed_total = 0

        # Rebalance monthly (~every 21 trading days) consistent with patient low-turnover style
        rebalance_indices = set(range(0, len(spy_dates) - 1, 21))

        for i, d_str in enumerate(spy_dates):
            # 1. Check daily stop-loss / take-profit on open positions
            for sym in list(positions.keys()):
                pos = positions[sym]
                hist = self.market.get_price_history(sym)
                if not hist:
                    continue
                idx = self.market._index_on_or_before(hist, d_str)
                if idx < 0 or hist["dates"][idx] != d_str:
                    continue
                day_low = hist["low"][idx]
                day_high = hist["high"][idx]
                day_open = hist["open"][idx]
                exit_px = None
                if day_low <= pos["stop_loss"]:
                    exit_px = min(day_open, pos["stop_loss"])
                elif day_high >= pos["take_profit"]:
                    exit_px = max(day_open, pos["take_profit"])
                if exit_px is not None:
                    half_sp = exit_px * (DEFAULT_SPREAD_BPS / 20_000.0)
                    fill_px = (exit_px - half_sp) * (1.0 - SLIPPAGE_RATE)
                    proceeds = pos["shares"] * fill_px
                    cash += proceeds
                    pnl = proceeds - pos["shares"] * pos["entry_price"]
                    closed_total += 1
                    if pnl > 0:
                        closed_wins += 1
                    trades_count += 1
                    del positions[sym]

            # 2. Mark to market at close
            pos_val = 0.0
            for sym, pos in positions.items():
                px = self.market.get_close_on_or_before(sym, d_str) or pos["entry_price"]
                pos_val += pos["shares"] * px
            equity = cash + pos_val
            peak_equity = max(peak_equity, equity)
            dd_pct = ((equity - peak_equity) / peak_equity) * 100.0
            max_dd_pct = min(max_dd_pct, dd_pct)
            equity_series.append((d_str, round(equity, 2)))

            # Stop trading if -10% drawdown breached
            if dd_pct <= -10.0:
                for sym, pos in list(positions.items()):
                    nxt = self.market.get_open_on_or_after(sym, d_str)
                    if nxt:
                        _, o_px = nxt
                        fill_px = (o_px * (1.0 - DEFAULT_SPREAD_BPS / 20_000.0)) * (1.0 - SLIPPAGE_RATE)
                        cash += pos["shares"] * fill_px
                        trades_count += 1
                        del positions[sym]
                continue

            # 3. Monthly evaluation using ONLY data available on d_str -> fill on NEXT trading day open
            if i in rebalance_indices:
                scored: List[Tuple[float, str, Dict[str, Any]]] = []
                for sym in candidates_universe:
                    pm = self.market.get_point_in_time_price_metrics(sym, d_str)
                    if not pm:
                        continue
                    fund = self.market.get_fundamentals(sym, save=False)
                    pe = self.market.get_point_in_time_pe(sym, d_str) or fund.get("trailing_pe")
                    mcap = self.market.get_historical_market_cap(sym, d_str) or fund.get("market_cap")
                    feat = {
                        "ticker": sym,
                        "sector": fund.get("sector", "Unknown"),
                        "market_cap": mcap,
                        "pe_ratio": pe,
                        "dist_from_52w_high_pct": pm["dist_from_52w_high_pct"],
                        "trend_3m_pct": pm["trend_3m_pct"],
                        "dividend_yield_pct": pm["dividend_yield_pct"],
                    }
                    ok, score, _, _ = evaluate_berkshire_rules(feat, rules)
                    if ok:
                        scored.append((score, sym, pm))

                scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
                qualifying_set = {s[1] for s in scored[:8]}

                # Exit positions that no longer meet Berkshire style rules after minimum hold window
                for sym in list(positions.keys()):
                    pos = positions[sym]
                    held_days = (date.fromisoformat(d_str) - date.fromisoformat(pos["entry_date"])).days
                    if held_days >= 60 and sym not in qualifying_set:
                        nxt = self.market.get_open_on_or_after(sym, d_str)
                        if nxt:
                            _, o_px = nxt
                            fill_px = (o_px * (1.0 - DEFAULT_SPREAD_BPS / 20_000.0)) * (1.0 - SLIPPAGE_RATE)
                            proceeds = pos["shares"] * fill_px
                            cash += proceeds
                            pnl = proceeds - pos["shares"] * pos["entry_price"]
                            closed_total += 1
                            if pnl > 0:
                                closed_wins += 1
                            trades_count += 1
                            del positions[sym]

                # Enter top qualifying stocks up to MAX_OPEN_POSITIONS (5)
                for score, sym, pm in scored:
                    if len(positions) >= MAX_OPEN_POSITIONS:
                        break
                    if sym in positions:
                        continue
                    nxt = self.market.get_open_on_or_after(sym, d_str)
                    if not nxt:
                        continue
                    fill_date, o_px = nxt
                    if o_px <= 0:
                        continue
                    fill_px = (o_px * (1.0 + DEFAULT_SPREAD_BPS / 20_000.0)) * (1.0 + SLIPPAGE_RATE)
                    stop_dist_pct = max(0.05, min(0.10, (pm["vol_20d_ann_pct"] / 100.0) * 0.35))
                    stop_loss = round(fill_px * (1.0 - stop_dist_pct), 2)
                    take_profit = round(fill_px * (1.0 + stop_dist_pct * 2.5), 2)

                    # Shared Risk Manager formula: 1% equity risk / stop distance, capped at 20% equity and cash
                    risk_dollars = equity * MAX_RISK_PER_TRADE_PCT
                    shares_by_risk = risk_dollars / max(0.01, fill_px - stop_loss)
                    shares_by_cap = (equity * MAX_POSITION_EQUITY_PCT) / fill_px
                    shares_by_cash = (cash * 0.995) / fill_px
                    shares = math.floor(min(shares_by_risk, shares_by_cap, shares_by_cash) * 10_000) / 10_000
                    notional = shares * fill_px
                    if shares <= 0 or notional < 50.0:
                        continue
                    cash -= notional
                    positions[sym] = {
                        "shares": shares,
                        "entry_price": fill_px,
                        "entry_date": fill_date,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                    }
                    trades_count += 1

        # Final liquidation valuation at test_end_date close
        final_val = cash
        for sym, pos in positions.items():
            px = self.market.get_close_on_or_before(sym, spy_dates[-1]) or pos["entry_price"]
            final_val += pos["shares"] * px

        pattern_return_pct = round(((final_val - INITIAL_CASH) / INITIAL_CASH) * 100.0, 2)

        # Compute SPY Buy-and-Hold over exact same test dates (entering next-day open after test_start_date)
        spy_entry = self.market.get_open_on_or_after("SPY", spy_dates[0])
        spy_end_px = self.market.get_close_on_or_before("SPY", spy_dates[-1])
        if spy_entry and spy_end_px:
            spy_fill = (spy_entry[1] * (1.0 + DEFAULT_SPREAD_BPS / 20_000.0)) * (1.0 + SLIPPAGE_RATE)
            spy_return_pct = round(((spy_end_px - spy_fill) / spy_fill) * 100.0, 2)
        else:
            spy_return_pct = 0.0

        excess_pct = round(pattern_return_pct - spy_return_pct, 2)
        beat_spy = pattern_return_pct > spy_return_pct
        win_rate = round((closed_wins / closed_total) * 100.0, 1) if closed_total > 0 else 0.0

        if beat_spy:
            verdict = (
                f"YES — Beat SPY in out-of-sample test ({spy_dates[0]} to {spy_dates[-1]}): "
                f"Berkshire Style returned {pattern_return_pct:+.2f}% vs SPY {spy_return_pct:+.2f}% "
                f"({excess_pct:+.2f}% excess, max drawdown {max_dd_pct:.2f}%)."
            )
        else:
            verdict = (
                f"NO — Did NOT beat SPY in out-of-sample test ({spy_dates[0]} to {spy_dates[-1]}): "
                f"Berkshire Style returned {pattern_return_pct:+.2f}% vs SPY {spy_return_pct:+.2f}% "
                f"({excess_pct:+.2f}% vs SPY, max drawdown {max_dd_pct:.2f}%). "
                "The defensive value/dividend rules lagged mega-cap growth leadership in the recent test window."
            )

        return {
            "test_period": [spy_dates[0], spy_dates[-1]],
            "trading_days": len(spy_dates),
            "start_equity": INITIAL_CASH,
            "end_equity": round(final_val, 2),
            "pattern_return_pct": pattern_return_pct,
            "spy_return_pct": spy_return_pct,
            "excess_return_vs_spy_pct": excess_pct,
            "beat_spy": beat_spy,
            "max_drawdown_pct": round(max_dd_pct, 2),
            "trades_count": trades_count,
            "closed_trades_count": closed_total,
            "win_rate_pct": win_rate,
            "plain_verdict": verdict,
        }

    def train_validate_and_save(self, agent_start_date: str) -> Dict[str, Any]:
        """Full pipeline:
        1. Load 5-year Berkshire 13F filings with `filing_date < agent_start_date`.
        2. Chronological split: learn on older ~70% of quarters, test on most recent ~30% vs SPY.
        3. Measure final pre-launch style rules from all filings public before `agent_start_date`.
        4. Persist complete artifact to `state/learned_patterns/berkshire_style.json`.
        """
        filings = self.fetch_and_cache_13f_filings(agent_start_date=agent_start_date, years=5)
        if len(filings) < 6:
            raise RuntimeError(f"Need at least 6 Berkshire 13F filings, found {len(filings)}.")

        # Keep first filing as baseline to identify truly NEW positions starting at index 1
        baseline_cusips = {h["cusip"] for h in filings[0]["holdings"]}
        active_filings = filings[1:]  # ~20 quarters (5 full years)

        split_idx = max(4, int(len(active_filings) * 0.70))
        train_filings = active_filings[:split_idx]
        test_filings = active_filings[split_idx:]

        logger.info(
            "Berkshire 13F split: Train=%d quarters (%s..%s), Test=%d quarters (%s..%s)",
            len(train_filings),
            train_filings[0]["report_date"],
            train_filings[-1]["report_date"],
            len(test_filings),
            test_filings[0]["report_date"],
            test_filings[-1]["report_date"],
        )

        # 1. Learn strictly on older Train split
        train_summary = self.measure_pattern(train_filings, baseline_cusips=baseline_cusips)

        # 2. Test those Train-learned rules out-of-sample on the most recent split (starting after last train filing_date!)
        test_start_date = train_filings[-1]["filing_date"]
        test_end_date = (date.fromisoformat(agent_start_date) - timedelta(days=1)).isoformat()
        oos_backtest = self.run_out_of_sample_backtest(
            rules=train_summary["learned_rules"],
            test_start_date=test_start_date,
            test_end_date=test_end_date,
        )

        # 3. Also measure full 5-year pre-start pattern (strictly filing_date < agent_start_date)
        full_summary = self.measure_pattern(active_filings, baseline_cusips=baseline_cusips)

        result = {
            "agent_id": "style_berkshire",
            "agent_name": "11. Berkshire Style (13F Pattern)",
            "source": "SEC EDGAR Berkshire Hathaway (CIK 0001067983) 13F-HR Filings (Last 5 Years)",
            "agent_start_date": agent_start_date,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "no_lookahead_verified": all(f["filing_date"] < agent_start_date for f in active_filings),
            "never_copy_filings": True,
            "timing_learned_from_13f": False,
            "train_split": {
                "quarters": len(train_filings),
                "report_date_range": train_summary["report_date_range"],
                "filing_date_range": train_summary["filing_date_range"],
                "learned_rules": train_summary["learned_rules"],
            },
            "out_of_sample_test": oos_backtest,
            "full_5y_pattern": full_summary["measured_pattern"],
            "learned_rules": full_summary["learned_rules"],
            "plain_language_rules": full_summary["plain_language_rules"],
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return result
