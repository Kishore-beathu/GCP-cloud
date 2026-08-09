"""Build data/tickers.csv from Finnhub's US symbol directory.

The app tracks whatever ``backend/data/tickers.csv`` contains (falling back to
the built-in ~45-symbol seed list). This script grows that file toward the
full pharma/life-sciences/AI universe using a single Finnhub API call, filtered
by keywords against each security's description.

Usage::

    FINNHUB_API_KEY=... python scripts/build_universe.py                # default keywords
    FINNHUB_API_KEY=... python scripts/build_universe.py --keywords pharma,bio,genomics
    FINNHUB_API_KEY=... python scripts/build_universe.py --limit 1000 --dry-run

Review the output before committing it — keyword matching casts a wide net on
purpose, and it is easier to delete rows than to notice missing ones.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import httpx

SYMBOLS_URL = "https://finnhub.io/api/v1/stock/symbol"
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "tickers.csv"

DEFAULT_KEYWORDS = (
    "pharma", "pharmaceutical", "bio", "therapeutic", "therapeutics", "medicine",
    "medical", "health", "genomic", "genetics", "oncology", "immuno", "vaccine",
    "diagnostic", "laborator", "life science", "clinical", "drug",
)

# Share classes, warrants, and units add noise without adding coverage.
EXCLUDED_TYPES = {"", "PUBLIC", None}
ALLOWED_TYPES = {"Common Stock", "ADR"}


def fetch_symbols(api_key: str, exchange: str) -> list[dict]:
    response = httpx.get(
        SYMBOLS_URL,
        params={"exchange": exchange, "token": api_key},
        timeout=60.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise SystemExit(f"Unexpected Finnhub response: {payload!r}")
    return payload


def matches(description: str, keywords: tuple[str, ...]) -> bool:
    lowered = description.lower()
    return any(keyword in lowered for keyword in keywords)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", default="US")
    parser.add_argument(
        "--keywords",
        default=",".join(DEFAULT_KEYWORDS),
        help="Comma-separated substrings matched against the security description",
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true", help="Print instead of writing")
    args = parser.parse_args()

    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise SystemExit("Set FINNHUB_API_KEY in the environment first")

    keywords = tuple(k.strip().lower() for k in args.keywords.split(",") if k.strip())
    symbols = fetch_symbols(api_key, args.exchange)
    print(f"Fetched {len(symbols)} symbols from Finnhub ({args.exchange})", file=sys.stderr)

    rows: list[dict[str, str]] = []
    for item in symbols:
        if item.get("type") not in ALLOWED_TYPES:
            continue
        ticker = str(item.get("symbol") or "").strip().upper()
        description = str(item.get("description") or "").strip()
        if not ticker or "." in ticker or not matches(description, keywords):
            continue
        rows.append(
            {
                "ticker": ticker,
                "company_name": description.title(),
                "sector": "life_sciences",
                "exchange": str(item.get("mic") or args.exchange),
            }
        )
        if len(rows) >= args.limit:
            break

    rows.sort(key=lambda row: row["ticker"])
    print(f"Matched {len(rows)} tickers", file=sys.stderr)

    if args.dry_run:
        for row in rows[:50]:
            print(row["ticker"], "-", row["company_name"])
        if len(rows) > 50:
            print(f"... and {len(rows) - 50} more")
        return

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["ticker", "company_name", "sector", "exchange"]
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} tickers to {OUTPUT}", file=sys.stderr)
    print("Restart the API (or call POST /admin/seed) to load them.", file=sys.stderr)


if __name__ == "__main__":
    main()
