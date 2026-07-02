"""Entrypoint for the weekly ISPE / pharma digest job.

Usage:
    python -m pharma_report.main              # fetch, render, upload to GCS
    python -m pharma_report.main --dry-run     # fetch, render, write to ./out/ instead
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from .claude_client import fetch_digest
from .config import load_config
from .render import render_html, render_json
from .storage import upload_to_gcs, write_local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write output to ./out/ instead of uploading to GCS.",
    )
    parser.add_argument(
        "--out-dir",
        default="out",
        help="Local output directory when --dry-run is set (default: ./out).",
    )
    args = parser.parse_args(argv)

    config = load_config()

    if not args.dry_run and not config.gcs_bucket:
        print("REPORT_BUCKET is not set (required unless --dry-run).", file=sys.stderr)
        return 2

    print(f"Fetching digest with model={config.model} ...", file=sys.stderr)
    digest = fetch_digest(config)
    digest.setdefault("generated_at", dt.datetime.now(dt.timezone.utc).isoformat())

    html = render_html(digest)
    json_str = render_json(digest)
    date_str = digest.get("week_of") or dt.date.today().isoformat()

    if args.dry_run:
        written = write_local(Path(args.out_dir), date_str, html, json_str)
    else:
        written = upload_to_gcs(config.gcs_bucket, date_str, html, json_str)

    print(f"ISPE events: {len(digest.get('ispe_events', []))}", file=sys.stderr)
    print(f"Pharma news items: {len(digest.get('pharma_news', []))}", file=sys.stderr)
    for path in written:
        print(f"Wrote {path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
