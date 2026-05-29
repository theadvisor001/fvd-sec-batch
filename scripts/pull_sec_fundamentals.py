#!/usr/bin/env python3
"""
FVD SEC Fundamentals Nightly Batch

Pulls every ticker's last ~5 years of canonical fundamentals from SEC EDGAR
XBRL companyfacts and writes a single JSON bundle the dashboard consumes.

Usage:
    SEC_UA="FVD/1.0 you@email.com" python pull_sec_fundamentals.py tickers.txt out.json

Schema (one entry per ticker):
{
    "AAPL": {
        "cik": 320193,
        "name": "Apple Inc.",
        "fetched_at": "2026-05-25T06:30:00Z",
        "fundamentals": {
            "Revenues":         [{"end":"2025-09-28","val":383285000000,"fy":2025,"fp":"FY"}],
            "NetIncomeLoss":    [{...}],
            "OperatingCashFlow":[{...}],
            "CashAndCashEquivalents": [...],
            "LongTermDebt":     [...],
            "StockholdersEquity":[...],
            "EarningsPerShareDiluted":[...],
            "CommonStockSharesOutstanding":[...],
            "Assets":           [...],
            "Liabilities":      [...],
            "GrossProfit":      [...],
            "ResearchAndDevelopmentExpense":[...]
        },
        "derived": {
            "fcf_ttm_usd":                  ...,
            "cfo_usd":                      ...,
            "capex_usd":                    ...,
            "net_debt_usd":                 ...,
            "adj_net_debt_usd":             ...,
            "total_debt_usd":               ...,
            "cash_usd":                     ...,
            "book_value_per_share":         ...,
            "tangible_book_value_per_share":...,
            "working_capital_usd":          ...,
            "current_ratio":                ...,
            "ebitda_usd":                   ...,
            "interest_coverage":            ...,
            "effective_tax_rate_pct":       ...,
            "rev_5y_cagr_pct":              ...
        }
    },
    ...
}
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SEC_BASE = "https://data.sec.gov"
SEC_UA = os.environ.get("SEC_UA", "").strip()
if not SEC_UA:
    print("FATAL: SEC_UA environment variable required (e.g. 'FVD/1.0 you@email.com')")
    print("  Set it as a GitHub Actions secret named SEC_UA in the repo settings.")
    sys.exit(1)
print(f"Using SEC_UA (length={len(SEC_UA)})")

HEADERS = {"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}

# Concepts to extract from XBRL companyfacts (us-gaap taxonomy)
GAAP_CONCEPTS = [
    # ── Income statement ──────────────────────────────────────────
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",  # newer name
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "GrossProfit",
    "ResearchAndDevelopmentExpense",
    "InterestExpense",
    "IncomeTaxExpenseBenefit",
    "DepreciationDepletionAndAmortization",
    "ShareBasedCompensation",
    # ── Balance sheet ─────────────────────────────────────────────
    "CashAndCashEquivalentsAtCarryingValue",
    "ShortTermInvestments",
    "AssetsCurrent",
    "Assets",
    "Goodwill",
    "IntangibleAssetsNetExcludingGoodwill",
    "LiabilitiesCurrent",
    "Liabilities",
    "LongTermDebtNoncurrent",
    "LongTermDebt",
    "LongTermDebtCurrent",
    "OperatingLeaseLiabilityNoncurrent",
    "StockholdersEquity",
    # ── Per-share & shares ─────────────────────────────────────────
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    # ── Cash flow ─────────────────────────────────────────────────
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
]


def load_cik_map() -> dict:
    """SEC publishes a master ticker → CIK mapping at company_tickers.json.
    Note: this file lives at www.sec.gov, not data.sec.gov."""
    r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
    r.raise_for_status()
    raw = r.json()
    out = {}
    # The structure is {idx: {cik_str, ticker, title}}
    for v in raw.values():
        out[v["ticker"].upper()] = {"cik": int(v["cik_str"]), "name": v["title"]}
    return out


def fetch_companyfacts(cik: int) -> dict:
    """Pull the full XBRL fact set for one CIK (10-digit zero-padded)."""
    padded = str(cik).zfill(10)
    url = f"{SEC_BASE}/api/xbrl/companyfacts/CIK{padded}.json"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == 2:
                print(f"  FAIL {cik}: {e}")
                return None
            time.sleep(2 ** attempt)
    return None


def extract_series(facts: dict, concept: str, unit: str = "USD") -> list:
    """Pull a clean time series for one GAAP concept, sorted newest first.
    Filters: 10-K / 10-Q only, last 5 fiscal years."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    entry = gaap.get(concept)
    if not entry:
        return []
    units_dict = entry.get("units", {})
    series = units_dict.get(unit) or units_dict.get("shares") or []
    cutoff_year = datetime.now(timezone.utc).year - 5
    cleaned = []
    for row in series:
        if row.get("fp") not in ("FY", "Q1", "Q2", "Q3", "Q4"):
            continue
        if row.get("fy", 0) < cutoff_year:
            continue
        if row.get("form") not in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
            continue
        cleaned.append({
            "end": row.get("end"),
            "val": row.get("val"),
            "fy":  row.get("fy"),
            "fp":  row.get("fp"),
            "form": row.get("form"),
        })
    cleaned.sort(key=lambda x: x.get("end", ""), reverse=True)
    return cleaned[:20]  # at most 5 years × 4 quarters


def _v(series: list, idx: int = 0) -> float | None:
    """Return the numeric value at series[idx], or None if missing/zero."""
    if not series or idx >= len(series):
        return None
    val = series[idx].get("val")
    return float(val) if val is not None and val == val else None  # guard NaN


def derive_metrics(fundamentals: dict) -> dict:
    """Compute enriched derived metrics the dashboard consumes.

    v39 additions: EBITDA, interest coverage, effective tax rate,
    tangible book value per share, working capital, current ratio,
    adjusted (net) debt including lease liabilities.
    """
    derived = {}

    # ── helpers ─────────────────────────────────────────────────────
    def v(key, idx=0):
        return _v(fundamentals.get(key, []), idx)

    def first(*keys):
        """Return the first non-None value from the listed series."""
        for k in keys:
            val = v(k)
            if val is not None:
                return val
        return None

    # ── Cash flow items ──────────────────────────────────────────────
    cfo   = v("NetCashProvidedByUsedInOperatingActivities")
    capex = v("PaymentsToAcquirePropertyPlantAndEquipment")
    if cfo is not None and capex is not None:
        derived["fcf_ttm_usd"] = cfo - capex
    if cfo is not None:
        derived["cfo_usd"] = cfo
    if capex is not None:
        derived["capex_usd"] = capex

    # ── Debt & cash ─────────────────────────────────────────────────
    lt_debt  = first("LongTermDebt", "LongTermDebtNoncurrent")
    st_debt  = first("LongTermDebtCurrent")
    leases   = v("OperatingLeaseLiabilityNoncurrent")
    cash     = first("CashAndCashEquivalentsAtCarryingValue")
    st_inv   = v("ShortTermInvestments")

    total_debt = (lt_debt or 0) + (st_debt or 0)
    liquid     = (cash or 0) + (st_inv or 0)
    if lt_debt is not None or st_debt is not None:
        derived["total_debt_usd"] = total_debt
    if cash is not None:
        derived["cash_usd"] = cash
    if liquid > 0:
        derived["net_debt_usd"] = total_debt - liquid
    # Adjusted net debt includes operating lease liabilities
    if leases is not None and liquid > 0:
        derived["adj_net_debt_usd"] = total_debt + (leases or 0) - liquid

    # ── Equity / balance sheet ───────────────────────────────────────
    equity     = v("StockholdersEquity")
    goodwill   = v("Goodwill")
    intangibles= v("IntangibleAssetsNetExcludingGoodwill")
    assets     = v("Assets")
    curr_assets= v("AssetsCurrent")
    curr_liabs = v("LiabilitiesCurrent")
    shares     = first("CommonStockSharesOutstanding",
                       "WeightedAverageNumberOfDilutedSharesOutstanding")

    if equity is not None and shares and shares > 0:
        derived["book_value_per_share"] = equity / shares
        gw  = goodwill   or 0
        ia  = intangibles or 0
        tbv = equity - gw - ia
        derived["tangible_book_value_per_share"] = tbv / shares
    if curr_assets is not None and curr_liabs is not None:
        derived["working_capital_usd"] = curr_assets - curr_liabs
        if curr_liabs > 0:
            derived["current_ratio"] = round(curr_assets / curr_liabs, 3)

    # ── Income statement ─────────────────────────────────────────────
    ebit      = v("OperatingIncomeLoss")
    ni        = v("NetIncomeLoss")
    da        = v("DepreciationDepletionAndAmortization")
    interest  = v("InterestExpense")
    tax_exp   = v("IncomeTaxExpenseBenefit")

    # EBITDA = EBIT + D&A (fallback: NI + tax + interest + D&A)
    if ebit is not None and da is not None:
        derived["ebitda_usd"] = ebit + da
    elif ni is not None and da is not None:
        adj = (tax_exp or 0) + abs(interest or 0)
        derived["ebitda_usd"] = ni + adj + da

    # Interest coverage = EBIT / |interest expense|
    if ebit is not None and interest is not None and abs(interest) > 0:
        derived["interest_coverage"] = round(ebit / abs(interest), 2)

    # Effective tax rate = tax expense / pre-tax income
    if ni is not None and tax_exp is not None:
        pretax = ni + (tax_exp or 0)
        if pretax != 0:
            derived["effective_tax_rate_pct"] = round(tax_exp / pretax * 100, 2)

    # ── Revenue CAGR (5-year) ─────────────────────────────────────────
    rev = (fundamentals.get("Revenues", [])
           or fundamentals.get("RevenueFromContractWithCustomerExcludingAssessedTax", []))
    # Filter to annual (FY) filings only for CAGR stability
    rev_fy = [r for r in rev if r.get("fp") == "FY"]
    if len(rev_fy) >= 2:
        recent = rev_fy[0].get("val")
        oldest = rev_fy[-1].get("val")
        if recent and oldest and oldest > 0:
            years = max(1, (rev_fy[0].get("fy", 0) - rev_fy[-1].get("fy", 0)))
            if years > 0:
                cagr = ((recent / oldest) ** (1 / years) - 1) * 100
                derived["rev_5y_cagr_pct"] = round(cagr, 2)

    return derived


def main():
    if len(sys.argv) != 3:
        print("usage: pull_sec_fundamentals.py <tickers.txt> <out.json>")
        sys.exit(1)
    in_path, out_path = sys.argv[1], sys.argv[2]
    if not Path(in_path).exists():
        print(f"FATAL: tickers file not found: {in_path}")
        sys.exit(1)
    tickers = [line.strip().upper() for line in Path(in_path).read_text().splitlines() if line.strip() and not line.startswith("#")]
    print(f"Tickers to process: {len(tickers)} — {tickers}")
    print(f"Loading CIK map …")
    try:
        cik_map = load_cik_map()
    except Exception as e:
        print(f"FATAL: could not load SEC CIK map: {e}")
        sys.exit(1)
    bundle = {}
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i, ticker in enumerate(tickers):
        info = cik_map.get(ticker)
        if not info:
            print(f"[{i+1}/{len(tickers)}] {ticker}: NOT IN SEC TICKER MAP — skipped")
            continue
        print(f"[{i+1}/{len(tickers)}] {ticker} (CIK {info['cik']}) …", end="", flush=True)
        facts = fetch_companyfacts(info["cik"])
        if facts is None:
            print(" no companyfacts (skipped)")
            continue
        fundamentals = {c: extract_series(facts, c) for c in GAAP_CONCEPTS}
        fundamentals = {k: v for k, v in fundamentals.items() if v}
        derived = derive_metrics(fundamentals)
        bundle[ticker] = {
            "cik": info["cik"],
            "name": info["name"],
            "fetched_at": now_iso,
            "fundamentals": fundamentals,
            "derived": derived,
        }
        print(f" ok ({len(fundamentals)} concepts, {len(derived)} derived)")
        time.sleep(0.12)  # SEC rate limit: 10 req/s
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({"version": 1, "generated_at": now_iso, "tickers": bundle}, indent=2))
    print(f"\nWrote {len(bundle)} tickers → {out_path}")


if __name__ == "__main__":
    main()
