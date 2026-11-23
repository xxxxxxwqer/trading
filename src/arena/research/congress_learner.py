"""Congress Style Pattern Learner (Agent 12): learns buying style from 2 years of House PTR filings.

Hard rules enforced:
- NEVER copy trades from PTR filings; filings are used ONLY as historical training data to measure
  Congress stock-buying patterns (sectors, company size, 30-day pre-trade price move, and typical
  holding time between a purchase and a later sale of the same stock by the same member).
- Shares only (`[ST]` stock transactions), all US House members, last 2 years.
- Parse each PTR PDF ONCE and permanently cache parsed transactions to `data_cache/congress_ptr_parsed.json`.
- Strictly no look-ahead: only filings with `filing_date < agent_start_date` are used.
- Chronological Train / Out-of-Sample Test split: learn rules on older history, test on the most
  recent period across UNIVERSE using normal price data, and report plainly whether it beat SPY.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import statistics
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pypdf import PdfReader

from arena.config import (
    DEFAULT_SPREAD_BPS,
    INITIAL_CASH,
    LIQUID_LARGE_CAP_STOCKS,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_EQUITY_PCT,
    MAX_RISK_PER_TRADE_PCT,
    SLIPPAGE_RATE,
    STATE_DIR,
)
from arena.research.market_history import (
    DATA_CACHE_DIR,
    WEB_HEADERS,
    MarketHistoryCache,
    classify_market_cap,
)

logger = logging.getLogger(__name__)

CONGRESS_PTR_CACHE_PATH = DATA_CACHE_DIR / "congress_ptr_parsed.json"
CONGRESS_PATTERN_PATH = STATE_DIR / "learned_patterns" / "congress_style.json"

# Regex matching House Clerk electronic PTR stock rows: "(TICKER) [ST]" followed by P/S and MM/DD/YYYY dates
_TX_BLOCK_RE = re.compile(
    r"\(([A-Z][A-Z0-9.\-]{0,5})\)\s*\[ST\]\s+"
    r"(P(?:\s*\(partial\))?|S(?:\s*\(partial\))?|E)\s+"
    r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+"
    r"(\$[0-9,]+\s*-\s*\$[0-9,]+|Over\s+\$[0-9,]+)",
    re.MULTILINE,
)

_OPTION_WORDS = ("option", "call ", "put ", "warrant", "future", "crypto", "bond", "note", "treasury")


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


def _parse_us_date(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_ptr_pdf_bytes(
    pdf_bytes: bytes,
    doc_id: str,
    member_name: str,
    state_dst: str,
    filing_date: str,
    year: int,
) -> List[Dict[str, Any]]:
    """Extract shares-only (`[ST]`) purchase and sale rows from a single House PTR PDF."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        full_text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return []

    if not full_text or "[ST]" not in full_text:
        return []

    # Collapse weird whitespace/newlines while keeping spaces between fields
    normalized = re.sub(r"[ \t\r\n]+", " ", full_text)
    rows: List[Dict[str, Any]] = []

    for match in _TX_BLOCK_RE.finditer(normalized):
        ticker = match.group(1).strip().upper().replace("-", ".")
        raw_type = match.group(2).strip().upper()
        tx_date = _parse_us_date(match.group(3))
        notif_date = _parse_us_date(match.group(4))
        amount_range = match.group(5).strip()

        if not tx_date or not ticker:
            continue

        # Double-check surrounding snippet to exclude options/derivatives (shares only!)
        ctx_start = max(0, match.start() - 120)
        ctx_end = min(len(normalized), match.end() + 80)
        snippet = normalized[ctx_start:ctx_end].lower()
        if any(w in snippet for w in _OPTION_WORDS):
            continue

        if raw_type.startswith("P"):
            tx_type = "PURCHASE"
        elif raw_type.startswith("S"):
            tx_type = "SALE"
        else:
            continue

        rows.append(
            {
                "doc_id": doc_id,
                "year": year,
                "member": member_name,
                "state_dst": state_dst,
                "filing_date": filing_date,
                "trade_date": tx_date,
                "notification_date": notif_date or filing_date,
                "ticker": ticker,
                "asset_type": "ST",
                "tx_type": tx_type,
                "amount_range": amount_range,
            }
        )
    return rows


def evaluate_congress_rules(
    stock_features: Dict[str, Any],
    rules: Dict[str, Any],
) -> Tuple[bool, float, List[str], List[str]]:
    """Explicit, testable rule evaluation for Congress Style on any candidate stock.

    Returns (matches_all_core_rules, score_0_to_100, passed_checks, failed_checks).
    NEVER checks whether any member of Congress bought the stock.
    """
    passed: List[str] = []
    failed: List[str] = []
    score = 0.0

    sector = str(stock_features.get("sector") or "Unknown")
    mcap = stock_features.get("market_cap")
    move_30d = stock_features.get("move_30d_pct")
    vol_ratio = stock_features.get("volume_ratio", 1.0)
    rs_vs_spy = stock_features.get("relative_strength_vs_spy", 0.0)

    # Rule 1: Preferred sector match
    preferred_sectors = list(rules.get("preferred_sectors") or [])
    if sector in preferred_sectors:
        passed.append(f"Sector '{sector}' matches learned Congress sectors ({', '.join(preferred_sectors)})")
        rank_bonus = max(0.0, 5.0 - preferred_sectors.index(sector)) * 2.5
        score += 32.0 + rank_bonus
    else:
        failed.append(f"Sector '{sector}' is outside learned Congress sectors ({', '.join(preferred_sectors)})")

    # Rule 2: Company size
    min_mcap = float(rules.get("min_market_cap_usd", 10_000_000_000.0))
    if mcap is not None and float(mcap) >= min_mcap:
        passed.append(f"Market cap ${float(mcap)/1e9:.1f}B >= ${min_mcap/1e9:.1f}B floor")
        # Slight preference for mega-cap liquid leaders when learned size is mega/large
        mcap_bonus = 6.0 if float(mcap) >= 100_000_000_000.0 else 2.0
        score += 22.0 + mcap_bonus
    else:
        mcap_str = f"${float(mcap)/1e9:.1f}B" if mcap else "unknown"
        failed.append(f"Market cap {mcap_str} below ${min_mcap/1e9:.1f}B floor")

    # Rule 3: 30-day pre-trade price move regime learned from historical House PTR purchases
    min_30d = float(rules.get("min_pre_trade_30d_move_pct", -10.0))
    max_30d = float(rules.get("max_pre_trade_30d_move_pct", 15.0))
    target_30d = float(rules.get("target_pre_trade_30d_move_pct", 2.0))
    if move_30d is not None and min_30d <= float(move_30d) <= max_30d:
        passed.append(f"30-day price move {float(move_30d):+.1f}% within learned [{min_30d:+.1f}%, {max_30d:+.1f}%]")
        # Score closer to the learned sweet-spot move higher
        closeness = max(0.0, 1.0 - abs(float(move_30d) - target_30d) / max(5.0, (max_30d - min_30d)))
        score += 26.0 + round(closeness * 10.0, 2)
    else:
        m_str = f"{float(move_30d):+.1f}%" if move_30d is not None else "unavailable"
        failed.append(f"30-day price move {m_str} outside learned [{min_30d:+.1f}%, {max_30d:+.1f}%]")

    # Tie-breaker bonus for healthy relative strength and volume participation
    if rs_vs_spy is not None and float(rs_vs_spy) > 0:
        score += min(4.0, float(rs_vs_spy) * 0.5)
    if vol_ratio is not None and float(vol_ratio) >= 1.0:
        score += min(3.0, (float(vol_ratio) - 1.0) * 2.0)

    matches = len(failed) == 0
    return matches, round(min(100.0, score), 2), passed, failed


class CongressStyleLearner:
    """Downloads and parses House PTR PDFs ONCE (caching results), measures the 2-year Congress
    stock-buying pattern, runs an out-of-sample split test vs SPY, and saves explicit rules."""

    def __init__(
        self,
        market_cache: Optional[MarketHistoryCache] = None,
        ptr_cache_path: Path = CONGRESS_PTR_CACHE_PATH,
        output_path: Path = CONGRESS_PATTERN_PATH,
        offline: bool = False,
    ) -> None:
        self.market = market_cache or MarketHistoryCache(offline=offline)
        self.ptr_cache_path = Path(ptr_cache_path)
        self.output_path = Path(output_path)
        self.offline = offline

    def _load_ptr_cache(self) -> Dict[str, Any]:
        if self.ptr_cache_path.exists():
            try:
                with self.ptr_cache_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("parsed_docs"), dict):
                    return data
            except Exception:
                pass
        return {"parsed_docs": {}, "index_meta": {}}

    def _save_ptr_cache(self, cache: Dict[str, Any]) -> None:
        self.ptr_cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ptr_cache_path.open("w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)

    def fetch_and_cache_ptr_transactions(
        self,
        agent_start_date: str,
        years: int = 2,
        max_workers: int = 12,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Download official House Clerk XML indexes for the last 2 years, parse every PTR PDF ONCE,
        cache the extracted `[ST]` share transactions, and strictly filter to `filing_date < agent_start_date`.
        """
        cache = self._load_ptr_cache()
        parsed_docs: Dict[str, Any] = cache["parsed_docs"]

        start_dt = date.fromisoformat(agent_start_date)
        oldest_dt = start_dt - timedelta(days=years * 365 + 15)
        oldest_str = oldest_dt.isoformat()
        target_years = list(range(oldest_dt.year, start_dt.year + 1))

        index_entries: List[Dict[str, Any]] = []
        if not self.offline:
            for yr in target_years:
                zip_url = f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{yr}FD.zip"
                try:
                    req = urllib.request.Request(zip_url, headers=WEB_HEADERS)
                    with urllib.request.urlopen(req, timeout=25) as resp:
                        zbytes = resp.read()
                    zf = zipfile.ZipFile(io.BytesIO(zbytes))
                    xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
                    if not xml_names:
                        continue
                    root = ET.fromstring(zf.read(xml_names[0]))
                    for m in root.findall("Member"):
                        ftype = (m.findtext("FilingType") or "").strip().upper()
                        if ftype != "P":
                            continue
                        doc_id = (m.findtext("DocID") or "").strip()
                        fdate = _parse_us_date(m.findtext("FilingDate") or "")
                        if not doc_id or not fdate:
                            continue
                        # Strictly no look-ahead: only filings public BEFORE agent_start_date
                        if fdate >= agent_start_date or fdate < oldest_str:
                            continue
                        first = (m.findtext("First") or "").strip()
                        last = (m.findtext("Last") or "").strip()
                        member_name = f"{first} {last}".strip()
                        state_dst = (m.findtext("StateDst") or "").strip()
                        index_entries.append(
                            {
                                "doc_id": doc_id,
                                "year": yr,
                                "filing_date": fdate,
                                "member": member_name,
                                "state_dst": state_dst,
                            }
                        )
                except Exception as exc:
                    logger.warning("Failed to fetch House %dFD.zip (%s)", yr, exc)

            # Determine which PTR PDFs have NOT been parsed & cached yet
            to_fetch = [e for e in index_entries if e["doc_id"] not in parsed_docs]
            logger.info(
                "House PTR index (%s..%s): %d filings total, %d already cached, %d PDFs to parse once...",
                oldest_str,
                agent_start_date,
                len(index_entries),
                len(index_entries) - len(to_fetch),
                len(to_fetch),
            )

            def _fetch_one(entry: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
                doc_id = entry["doc_id"]
                yr = entry["year"]
                url = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{yr}/{doc_id}.pdf"
                try:
                    req = urllib.request.Request(url, headers=WEB_HEADERS)
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        pbytes = resp.read()
                    txs = parse_ptr_pdf_bytes(
                        pdf_bytes=pbytes,
                        doc_id=doc_id,
                        member_name=entry["member"],
                        state_dst=entry["state_dst"],
                        filing_date=entry["filing_date"],
                        year=yr,
                    )
                    return doc_id, {
                        "doc_id": doc_id,
                        "year": yr,
                        "filing_date": entry["filing_date"],
                        "member": entry["member"],
                        "state_dst": entry["state_dst"],
                        "status": "parsed",
                        "transactions": txs,
                    }
                except Exception as exc:
                    return doc_id, {
                        "doc_id": doc_id,
                        "year": yr,
                        "filing_date": entry["filing_date"],
                        "member": entry["member"],
                        "state_dst": entry["state_dst"],
                        "status": f"error:{type(exc).__name__}",
                        "transactions": [],
                    }

            if to_fetch:
                completed = 0
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = [pool.submit(_fetch_one, e) for e in to_fetch]
                    for fut in as_completed(futures):
                        doc_id, rec = fut.result()
                        parsed_docs[doc_id] = rec
                        completed += 1
                        if completed % 150 == 0:
                            logger.info("  Parsed %d/%d House PTR PDFs...", completed, len(to_fetch))
                            self._save_ptr_cache(cache)

                cache["index_meta"] = {
                    "last_synced_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "agent_start_date": agent_start_date,
                    "oldest_date": oldest_str,
                    "total_cached_pdfs": len(parsed_docs),
                }
                self._save_ptr_cache(cache)

        # Collect all shares-only transactions strictly obeying `filing_date < agent_start_date`
        all_txs: List[Dict[str, Any]] = []
        eligible_pdfs = 0
        parsed_ok_pdfs = 0
        for doc in parsed_docs.values():
            fdate = doc.get("filing_date", "9999-12-31")
            if fdate >= agent_start_date or fdate < oldest_str:
                continue
            eligible_pdfs += 1
            if doc.get("status") == "parsed":
                parsed_ok_pdfs += 1
            for tx in doc.get("transactions") or []:
                if tx.get("asset_type") == "ST" and tx.get("filing_date", "9999-12-31") < agent_start_date:
                    all_txs.append(tx)

        all_txs.sort(key=lambda x: (x["trade_date"], x["filing_date"], x["doc_id"]))
        stats = {
            "eligible_pdfs": eligible_pdfs,
            "parsed_ok_pdfs": parsed_ok_pdfs,
            "total_share_transactions": len(all_txs),
            "purchase_transactions": sum(1 for t in all_txs if t["tx_type"] == "PURCHASE"),
            "sale_transactions": sum(1 for t in all_txs if t["tx_type"] == "SALE"),
            "unique_members": len({t["member"] for t in all_txs}),
            "unique_tickers": len({t["ticker"] for t in all_txs}),
        }
        return all_txs, stats

    def measure_pattern(
        self,
        transactions: List[Dict[str, Any]],
        max_tickers_to_enrich: int = 160,
    ) -> Dict[str, Any]:
        """Measure Congress stock-buying pattern from a chronological slice of PTR `[ST]` transactions:
        - sectors
        - company size
        - stock's price move in the 30 days before the trade date
        - typical time between a purchase and a later sale of the same stock (same member)
        """
        purchases = [t for t in transactions if t["tx_type"] == "PURCHASE"]
        sales = [t for t in transactions if t["tx_type"] == "SALE"]
        if not purchases:
            raise ValueError("No eligible Congress stock purchase transactions found.")

        # 1. Typical time between a purchase and a later sale of the same stock by the same member
        sales_by_key: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        for s in sales:
            sales_by_key[(s["member"], s["ticker"])].append(s["trade_date"])
        for k in sales_by_key:
            sales_by_key[k].sort()

        hold_days_list: List[float] = []
        matched_roundtrips = 0
        for p in purchases:
            key = (p["member"], p["ticker"])
            p_date = p["trade_date"]
            later_sales = [sd for sd in sales_by_key.get(key, []) if sd > p_date]
            if later_sales:
                try:
                    delta = (date.fromisoformat(later_sales[0]) - date.fromisoformat(p_date)).days
                    if 1 <= delta <= 730:
                        hold_days_list.append(float(delta))
                        matched_roundtrips += 1
                except Exception:
                    pass

        # 2. Prioritize the most frequently purchased tickers (covers ~80%+ of all House purchases)
        ticker_counts = Counter(p["ticker"] for p in purchases)
        top_tickers = [t for t, _ in ticker_counts.most_common(max_tickers_to_enrich)]
        top_ticker_set = set(top_tickers)

        # Fetch fundamentals & price history (cached on disk)
        for sym in top_tickers:
            self.market.get_fundamentals(sym, save=False)
            self.market.get_price_history(sym)
        self.market._save_json(self.market.fundamentals_path, self.market._fundamentals)

        sector_counts: Counter = Counter()
        size_buckets: Counter = Counter()
        market_caps: List[float] = []
        pre_30d_moves: List[float] = []
        evaluated_purchases = 0

        for p in purchases:
            sym = p["ticker"]
            if sym not in top_ticker_set:
                continue
            fund = self.market.get_fundamentals(sym, save=False)
            if fund.get("quote_type") == "ETF":
                continue  # Shares only!
            sec = fund.get("sector") or "Unknown"
            if sec != "Unknown":
                sector_counts[sec] += 1

            tdate = p["trade_date"]
            mcap = self.market.get_historical_market_cap(sym, tdate) or fund.get("market_cap")
            if mcap and mcap > 0:
                market_caps.append(float(mcap))
                size_buckets[classify_market_cap(float(mcap))] += 1

            pm = self.market.get_point_in_time_price_metrics(sym, tdate)
            if pm and pm.get("move_30d_pct") is not None:
                pre_30d_moves.append(float(pm["move_30d_pct"]))
                evaluated_purchases += 1

        total_sec = sum(sector_counts.values()) or 1
        sector_dist_pct = {
            sec: round((cnt / total_sec) * 100.0, 2)
            for sec, cnt in sector_counts.most_common()
        }
        preferred_sectors = [sec for sec, _ in sector_counts.most_common(5)]

        median_30d = _median(pre_30d_moves, default=1.5)
        p25_30d = _quantile(pre_30d_moves, 0.25, default=-4.0)
        p75_30d = _quantile(pre_30d_moves, 0.75, default=8.0)
        pct_buying_positive_30d = (
            round((sum(1 for m in pre_30d_moves if m > 0) / len(pre_30d_moves)) * 100.0, 1)
            if pre_30d_moves
            else 50.0
        )

        median_hold_days = _median(hold_days_list, default=90.0)
        avg_hold_days = round(sum(hold_days_list) / len(hold_days_list), 1) if hold_days_list else 90.0
        p25_hold = _quantile(hold_days_list, 0.25, default=35.0)
        p75_hold = _quantile(hold_days_list, 0.75, default=180.0)

        mcap_median = _median(market_caps, default=120_000_000_000.0)
        mcap_p25 = _quantile(market_caps, 0.25, default=20_000_000_000.0)
        min_mcap_rule = round(max(10_000_000_000.0, min(50_000_000_000.0, mcap_p25)), 0)

        # Derive explicit 30-day pre-trade move band from empirical interquartile range
        min_30d_rule = round(max(-20.0, p25_30d - 2.0), 1)
        max_30d_rule = round(min(25.0, p75_30d + 2.0), 1)

        # Holding-horizon guidance for backtest & live exits
        exit_hold_days = int(max(15, min(180, round(median_hold_days))))

        learned_rules = {
            "never_copy_filings": True,
            "shares_only": True,
            "preferred_sectors": preferred_sectors,
            "min_market_cap_usd": min_mcap_rule,
            "min_pre_trade_30d_move_pct": min_30d_rule,
            "max_pre_trade_30d_move_pct": max_30d_rule,
            "target_pre_trade_30d_move_pct": median_30d,
            "typical_hold_days": exit_hold_days,
            "stop_loss_atr_mult": 2.0,
            "take_profit_pct": 6.5,
        }

        bias_label = (
            "mild upward momentum (buying winners)"
            if median_30d > 1.0
            else ("dip-buying / pullback entries" if median_30d < -1.0 else "neutral / steady price action")
        )
        plain_language_rules = [
            f"Focus on the top 5 sectors most frequently bought by US House members: {', '.join(preferred_sectors)} (never copy a ticker because a lawmaker bought it).",
            f"Target liquid large/mega-cap companies with market cap at least ${min_mcap_rule/1e9:.1f}B (measured median House purchase size: ${mcap_median/1e9:.1f}B).",
            f"Match Congress's pre-trade 30-day price setup ({bias_label}): enter stocks whose 30-day return is between {min_30d_rule:+.1f}% and {max_30d_rule:+.1f}% (historical median before trade date: {median_30d:+.1f}%, {pct_buying_positive_30d:.0f}% bought after positive 30-day moves).",
            f"Follow the measured purchase-to-sale holding cadence: median time between a member's buy and subsequent sale of the same stock is {median_hold_days:.0f} days (IQR {p25_hold:.0f}–{p75_hold:.0f} days across {matched_roundtrips} matched buy->sale pairs). Hold positions with medium-term patience unless the stop-loss, take-profit, or trend/momentum breakdown triggers.",
        ]

        return {
            "purchase_count": len(purchases),
            "sale_count": len(sales),
            "evaluated_purchases_with_prices": evaluated_purchases,
            "matched_purchase_to_sale_pairs": matched_roundtrips,
            "trade_date_range": [purchases[0]["trade_date"], purchases[-1]["trade_date"]],
            "filing_date_range": [
                min(t["filing_date"] for t in transactions),
                max(t["filing_date"] for t in transactions),
            ],
            "measured_pattern": {
                "sector_purchase_share_pct": sector_dist_pct,
                "sector_purchase_counts": dict(sector_counts.most_common()),
                "company_size_distribution": dict(size_buckets.most_common()),
                "median_purchase_market_cap_usd": mcap_median,
                "pre_trade_30d_move": {
                    "median_pct": median_30d,
                    "p25_pct": p25_30d,
                    "p75_pct": p75_30d,
                    "positive_30d_share_pct": pct_buying_positive_30d,
                    "sample_size": len(pre_30d_moves),
                },
                "purchase_to_sale_hold_days": {
                    "median_days": median_hold_days,
                    "avg_days": avg_hold_days,
                    "p25_days": p25_hold,
                    "p75_days": p75_hold,
                    "matched_pairs": matched_roundtrips,
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
        """Test learned Congress Style rules on the most recent held-out period across UNIVERSE
        using ONLY normal price/fundamental data available on each decision date, filling on next-day
        open with spread + 5 bps slippage and shared risk manager limits."""
        spy_hist = self.market.get_price_history("SPY")
        if not spy_hist:
            return {"error": "SPY price history unavailable for backtest."}

        spy_dates = [d for d in spy_hist["dates"] if test_start_date <= d <= test_end_date]
        if len(spy_dates) < 20:
            return {"error": f"Insufficient test trading days between {test_start_date} and {test_end_date}."}

        candidates_universe = list(LIQUID_LARGE_CAP_STOCKS)
        for sym in candidates_universe:
            self.market.get_price_history(sym)
            self.market.get_fundamentals(sym, save=False)

        cash = INITIAL_CASH
        positions: Dict[str, Dict[str, Any]] = {}
        peak_equity = INITIAL_CASH
        max_dd_pct = 0.0
        trades_count = 0
        closed_wins = 0
        closed_total = 0

        typical_hold_days = int(rules.get("typical_hold_days", 60))
        # Evaluate weekly (every 5 trading days) to reflect active multi-week Congressional swing cadence
        rebalance_indices = set(range(0, len(spy_dates) - 1, 5))

        for i, d_str in enumerate(spy_dates):
            # 1. Check stop-loss / take-profit and holding-horizon exits
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
                held_days = (date.fromisoformat(d_str) - date.fromisoformat(pos["entry_date"])).days
                exit_px = None
                if day_low <= pos["stop_loss"]:
                    exit_px = min(day_open, pos["stop_loss"])
                elif day_high >= pos["take_profit"]:
                    exit_px = max(day_open, pos["take_profit"])
                elif held_days >= typical_hold_days:
                    exit_px = day_open
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

            # 2. Mark to market
            pos_val = 0.0
            for sym, pos in positions.items():
                px = self.market.get_close_on_or_before(sym, d_str) or pos["entry_price"]
                pos_val += pos["shares"] * px
            equity = cash + pos_val
            peak_equity = max(peak_equity, equity)
            dd_pct = ((equity - peak_equity) / peak_equity) * 100.0
            max_dd_pct = min(max_dd_pct, dd_pct)

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

            # 3. Weekly scan on d_str -> fill on NEXT trading day open
            if i in rebalance_indices and len(positions) < MAX_OPEN_POSITIONS:
                spy_pm = self.market.get_point_in_time_price_metrics("SPY", d_str)
                spy_30d = spy_pm["move_30d_pct"] if spy_pm else 0.0
                scored: List[Tuple[float, str, Dict[str, Any]]] = []
                for sym in candidates_universe:
                    if sym in positions:
                        continue
                    pm = self.market.get_point_in_time_price_metrics(sym, d_str)
                    if not pm:
                        continue
                    fund = self.market.get_fundamentals(sym, save=False)
                    mcap = self.market.get_historical_market_cap(sym, d_str) or fund.get("market_cap")
                    feat = {
                        "ticker": sym,
                        "sector": fund.get("sector", "Unknown"),
                        "market_cap": mcap,
                        "move_30d_pct": pm["move_30d_pct"],
                        "relative_strength_vs_spy": round(pm["move_30d_pct"] - spy_30d, 2),
                        "volume_ratio": 1.0,
                    }
                    ok, score, _, _ = evaluate_congress_rules(feat, rules)
                    if ok:
                        scored.append((score, sym, pm))

                scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
                for score, sym, pm in scored:
                    if len(positions) >= MAX_OPEN_POSITIONS:
                        break
                    nxt = self.market.get_open_on_or_after(sym, d_str)
                    if not nxt:
                        continue
                    fill_date, o_px = nxt
                    if o_px <= 0:
                        continue
                    fill_px = (o_px * (1.0 + DEFAULT_SPREAD_BPS / 20_000.0)) * (1.0 + SLIPPAGE_RATE)
                    stop_dist_pct = max(0.04, min(0.08, (pm["vol_20d_ann_pct"] / 100.0) * 0.28))
                    stop_loss = round(fill_px * (1.0 - stop_dist_pct), 2)
                    take_profit = round(fill_px * (1.0 + stop_dist_pct * 2.2), 2)

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

        final_val = cash
        for sym, pos in positions.items():
            px = self.market.get_close_on_or_before(sym, spy_dates[-1]) or pos["entry_price"]
            final_val += pos["shares"] * px

        pattern_return_pct = round(((final_val - INITIAL_CASH) / INITIAL_CASH) * 100.0, 2)

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
                f"Congress Style returned {pattern_return_pct:+.2f}% vs SPY {spy_return_pct:+.2f}% "
                f"({excess_pct:+.2f}% excess, max drawdown {max_dd_pct:.2f}%)."
            )
        else:
            verdict = (
                f"NO — Did NOT beat SPY in out-of-sample test ({spy_dates[0]} to {spy_dates[-1]}): "
                f"Congress Style returned {pattern_return_pct:+.2f}% vs SPY {spy_return_pct:+.2f}% "
                f"({excess_pct:+.2f}% vs SPY, max drawdown {max_dd_pct:.2f}%). "
                "The learned sector/30-day-move pattern underperformed passive S&P 500 exposure after realistic trading friction."
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
        1. Download & parse 2 years of House PTR PDFs ONCE (cached to disk), strictly `filing_date < agent_start_date`.
        2. Chronological split by `filing_date`: learn on older ~65% (~16 months), test out-of-sample on most recent ~35% (~8 months) vs SPY.
        3. Measure final pre-launch style rules from all filings public before `agent_start_date`.
        4. Persist complete artifact to `state/learned_patterns/congress_style.json`.
        """
        all_txs, ptr_stats = self.fetch_and_cache_ptr_transactions(agent_start_date=agent_start_date, years=2)
        if not all_txs:
            raise RuntimeError("No eligible House PTR share transactions found.")

        start_dt = date.fromisoformat(agent_start_date)
        # Split 24 months into older 16 months (Train) and most recent 8 months (Out-of-Sample Test)
        split_cutoff = (start_dt - timedelta(days=240)).isoformat()
        train_txs = [t for t in all_txs if t["filing_date"] <= split_cutoff]
        test_txs = [t for t in all_txs if t["filing_date"] > split_cutoff]

        if len([t for t in train_txs if t["tx_type"] == "PURCHASE"]) < 30:
            # Fallback chronological 65/35 split by index
            sorted_by_fdate = sorted(all_txs, key=lambda x: x["filing_date"])
            idx = max(1, int(len(sorted_by_fdate) * 0.65))
            train_txs = sorted_by_fdate[:idx]
            test_txs = sorted_by_fdate[idx:]
            split_cutoff = train_txs[-1]["filing_date"]

        logger.info(
            "Congress PTR split (filing_date <= %s): Train=%d txs, Test=%d txs",
            split_cutoff,
            len(train_txs),
            len(test_txs),
        )

        # 1. Learn strictly on older Train split
        train_summary = self.measure_pattern(train_txs)

        # 2. Test those Train-learned rules out-of-sample on the most recent 8 months
        test_start_date = (date.fromisoformat(split_cutoff) + timedelta(days=1)).isoformat()
        test_end_date = (start_dt - timedelta(days=1)).isoformat()
        oos_backtest = self.run_out_of_sample_backtest(
            rules=train_summary["learned_rules"],
            test_start_date=test_start_date,
            test_end_date=test_end_date,
        )

        # 3. Also measure full 2-year pre-start pattern (strictly filing_date < agent_start_date)
        full_summary = self.measure_pattern(all_txs)

        result = {
            "agent_id": "style_congress",
            "agent_name": "12. Congress Style (House PTR Pattern)",
            "source": "US House Clerk Periodic Transaction Reports (Shares Only [ST], All Members, Last 2 Years)",
            "agent_start_date": agent_start_date,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "no_lookahead_verified": all(t["filing_date"] < agent_start_date for t in all_txs),
            "never_copy_filings": True,
            "shares_only": True,
            "pdf_cache_path": str(self.ptr_cache_path.relative_to(STATE_DIR.parent)).replace("\\", "/"),
            "ptr_corpus_stats": ptr_stats,
            "train_split": {
                "filing_date_cutoff": split_cutoff,
                "transactions_count": len(train_txs),
                "learned_rules": train_summary["learned_rules"],
            },
            "out_of_sample_test": oos_backtest,
            "full_2y_pattern": full_summary["measured_pattern"],
            "learned_rules": full_summary["learned_rules"],
            "plain_language_rules": full_summary["plain_language_rules"],
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        return result
