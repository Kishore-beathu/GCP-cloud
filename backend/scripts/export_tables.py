"""Export tables to CSV, for carrying data the vendors cannot give back.

Most of this platform's data is re-fetchable: the universe re-seeds from
`tickers.py`, price history comes back from a single Yahoo call. Two tables are
not.

* **intraday_bars.** Yahoo serves the current session and nothing earlier, so a
  session nobody recorded is gone permanently. Whatever has accumulated is the
  only copy that will ever exist.
* **trades.** The paper record the strategies are eventually judged on. No
  vendor has it.

The deployment guide used to suggest the `sqlite3` command-line tool for this.
That ships with most Unix systems and with none of the Windows installs this
project actually runs on, so the instruction failed where it was most needed.
This uses the standard library instead, which is present wherever the backend
runs at all.

Reads the configured DATABASE_URL rather than guessing a path, so it exports
from the same database the application uses.

    python scripts/export_tables.py
    python scripts/export_tables.py --tables trades --out backup/
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

# The two that cannot be re-fetched. Others can be named explicitly, but these
# are the ones whose loss is permanent.
DEFAULT_TABLES = ("intraday_bars", "trades", "portfolios")


def _sqlite_path(database_url: str) -> Path:
    """The file behind a SQLite URL.

    Raises for anything else: this is a local-to-production helper, and a
    Postgres database already has pg_dump, which does the job better.
    """
    if not database_url.startswith("sqlite"):
        raise SystemExit(
            f"DATABASE_URL is not SQLite ({database_url.split('://')[0]}://...).\n"
            "For PostgreSQL use pg_dump, which handles types and constraints "
            "properly."
        )
    # sqlite+aiosqlite:////abs/path.db or sqlite+aiosqlite:///relative.db
    _, _, tail = database_url.partition("://")
    return Path(tail.lstrip("/") if not tail.startswith("//") else tail[1:])


def export(path: Path, table: str, out_dir: Path) -> int:
    """Write one table to CSV. Returns the row count."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cursor = connection.execute(f'SELECT * FROM "{table}"')  # noqa: S608
        columns = [description[0] for description in cursor.description]
        target = out_dir / f"{table}.csv"
        rows = 0
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for row in cursor:
                writer.writerow(row)
                rows += 1
        return rows
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tables",
        nargs="*",
        default=list(DEFAULT_TABLES),
        help="Tables to export (default: the ones that cannot be re-fetched)",
    )
    parser.add_argument(
        "--out", default=".", help="Directory to write the CSV files into"
    )
    args = parser.parse_args()

    settings = get_settings()
    path = _sqlite_path(settings.database_url)
    if not path.exists():
        raise SystemExit(
            f"No database at {path}.\n"
            "DATABASE_URL points there but the file is missing — check you are "
            "running this from the backend directory."
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for table in args.tables:
        try:
            rows = export(path, table, out_dir)
        except sqlite3.OperationalError as exc:
            # A table that was never created is worth saying rather than
            # failing over: a portfolio nobody made is not an error.
            print(f"{table:20} skipped ({exc})")
            continue
        print(f"{table:20} {rows:>8,} rows -> {out_dir / f'{table}.csv'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
