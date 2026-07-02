"""Uploads the rendered report to GCS, and a local-filesystem fallback for --dry-run."""

from __future__ import annotations

from pathlib import Path


def upload_to_gcs(bucket_name: str, date_str: str, html: str, json_str: str) -> list[str]:
    from google.cloud import storage  # imported lazily so --dry-run doesn't need the dep configured

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    written = []
    for name, content, content_type in [
        (f"reports/{date_str}.html", html, "text/html; charset=utf-8"),
        (f"reports/{date_str}.json", json_str, "application/json"),
        ("reports/latest.html", html, "text/html; charset=utf-8"),
        ("reports/latest.json", json_str, "application/json"),
    ]:
        blob = bucket.blob(name)
        blob.upload_from_string(content, content_type=content_type)
        written.append(f"gs://{bucket_name}/{name}")

    return written


def write_local(out_dir: Path, date_str: str, html: str, json_str: str) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in [
        (f"{date_str}.html", html),
        (f"{date_str}.json", json_str),
        ("latest.html", html),
        ("latest.json", json_str),
    ]:
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return written
