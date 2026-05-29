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
            "fcf_ttm_usd":     ...,
            "net_debt_usd":    ...,
            "book_value_per_share": ...,
            "rev_5y_cagr_pct": ...
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
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",  # newer name
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "GrossProfit",
    "ResearchAndDevelopmentExpense",
    "CashAndCashEquivalentsAtCarryingValue",
    "LongTermDebtNoncurrent",
    "LongTermDebt",
    "StockholdersEquity",
    "Assets",
    "Liabilities",
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
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


def derive_metrics(fundamentals: dict) -> dict:
    """Compute the small set of derived metrics the dashboard wants."""
    derived = {}
    cfo_series = fundamentals.get("NetCashProvidedByUsedInOperatingActivities", [])
    capex_series = fundamentals.get("PaymentsToAcquirePropertyPlantAndEquipment", [])
    if cfo_series and capex_series:
        latest_cfo = cfo_series[0].get("val") or 0
        latest_capex = capex_series[0].get("val") or 0
        derived["fcf_ttm_usd"] = latest_cfo - latest_capex
    debt = (fundamentals.get("LongTermDebt", [])
            or fundamentals.get("LongTermDebtNoncurrent", []))
    cash = fundamentals.get("CashAndCashEquivalentsAtCarryingValue", [])
    if debt and cash:
        derived["net_debt_usd"] = (debt[0].get("val") or 0) - (cash[0].get("val") or 0)
    equity = fundamentals.get("StockholdersEquity", [])
    shares = (fundamentals.get("CommonStockSharesOutstanding", [])
              or fundamentals.get("WeightedAverageNumberOfDilutedSharesOutstanding", []))
    if equity and shares and shares[0].get("val"):
        derived["book_value_per_share"] = (equity[0].get("val") or 0) / shares[0]["val"]
    rev = (fundamentals.get("Revenues", [])
           or fundamentals.get("RevenueFromContractWithCustomerExcludingAssessedTax", []))
    if len(rev) >= 5:
        recent = rev[0].get("val")
        oldest = rev[-1].get("val")
        if recent and oldest and oldest > 0:
            years = max(1, (rev[0].get("fy", 0) - rev[-1].get("fy", 0)))
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
