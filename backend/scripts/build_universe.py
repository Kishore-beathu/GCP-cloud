"""Build data/tickers.csv from Finnhub's symbol directories.

The app tracks whatever ``backend/data/tickers.csv`` contains (falling back to
the built-in ~45-symbol seed list). This script grows that file toward the
full pharma/life-sciences/AI universe across Europe, North America and Asia,
filtered by keywords against each security's description.

The venue suffix is part of the symbol everywhere except the US (AZN.L,
NOVO-B.CO, 7203.T, SHOP.TO), and it is what resolves a listing's exchange,
region, country and currency.

Usage::

    # US only (the default)
    FINNHUB_API_KEY=... python scripts/build_universe.py

    # Europe, North America and Asia in one pass
    FINNHUB_API_KEY=... python scripts/build_universe.py \
        --exchange US,L,PA,AS,DE,SW,CO,ST,MI,MC,BR,HE,OL,TO,T,HK,SS,KS,NS,AX,SI,TW

    FINNHUB_API_KEY=... python scripts/build_universe.py --region europe --dry-run

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import markets  # noqa: E402  (after sys.path fix)

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
    parser.add_argument(
        "--exchange",
        default="US",
        help="Comma-separated Finnhub exchange codes, e.g. US,L,PA,DE,T,HK",
    )
    parser.add_argument(
        "--region",
        action="append",
        choices=["north_america", "europe", "asia_pacific"],
        help="Keep only these regions; repeatable. Default: keep everything.",
    )
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
    wanted_regions = set(args.region or ())

    exchanges = [code.strip() for code in args.exchange.split(",") if code.strip()]
    symbols: list[dict] = []
    for code in exchanges:
        fetched = fetch_symbols(api_key, code)
        print(f"Fetched {len(fetched)} symbols from Finnhub ({code})", file=sys.stderr)
        symbols.extend(fetched)

    rows: list[dict[str, str]] = []
    for item in symbols:
        if item.get("type") not in ALLOWED_TYPES:
            continue
        ticker = str(item.get("symbol") or "").strip().upper()
        description = str(item.get("description") or "").strip()
        if not ticker or not matches(description, keywords):
            continue

        # The venue suffix IS the symbol on every market except the US, so it
        # must survive: AZN.L, NOVO-B.CO, 7203.T, SHOP.TO.
        market = markets.resolve(ticker)
        if wanted_regions and market.region not in wanted_regions:
            continue

        rows.append(
            {
                "ticker": ticker,
                "company_name": description.title(),
                "sector": "life_sciences",
                "exchange": market.name,
                "mic": market.mic,
                "region": market.region,
                "country": market.country,
                "currency": market.currency,
            }
        )
        if len(rows) >= args.limit:
            break

    rows.sort(key=lambda row: (row["region"], row["ticker"]))
    by_region: dict[str, int] = {}
    for row in rows:
        by_region[row["region"]] = by_region.get(row["region"], 0) + 1
    print(f"Matched {len(rows)} tickers: {by_region}", file=sys.stderr)

    if args.dry_run:
        for row in rows[:50]:
            print(row["ticker"], "-", row["company_name"])
        if len(rows) > 50:
            print(f"... and {len(rows) - 50} more")
        return

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ticker", "company_name", "sector", "exchange",
                "mic", "region", "country", "currency",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} tickers to {OUTPUT}", file=sys.stderr)
    print("Restart the API (or call POST /admin/seed) to load them.", file=sys.stderr)


if __name__ == "__main__":
    main()
