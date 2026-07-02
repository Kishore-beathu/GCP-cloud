"""Renders the structured digest into HTML (and passes through the JSON)."""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_html(digest: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html.jinja")
    return template.render(
        week_of=digest.get("week_of", ""),
        generated_at=digest.get("generated_at", ""),
        ispe_events=digest.get("ispe_events", []),
        pharma_news=digest.get("pharma_news", []),
    )


def render_json(digest: dict) -> str:
    return json.dumps(digest, indent=2, ensure_ascii=False)
