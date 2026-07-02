import json
from pathlib import Path

from pharma_report.render import render_html, render_json

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sample_digest.json").read_text())


def test_render_html_includes_events_and_news():
    html = render_html(FIXTURE)
    assert "Annual Pharma Manufacturing Conference" in html
    assert "ISPE DACH" in html
    assert "EMA issues updated guidance on continuous manufacturing" in html
    assert "2026-07-06" in html


def test_render_html_handles_empty_sections():
    empty = {"week_of": "2026-07-06", "generated_at": "now", "ispe_events": [], "pharma_news": []}
    html = render_html(empty)
    assert "No qualifying chapter events found this week." in html
    assert "No qualifying news items found this week." in html


def test_render_json_round_trips():
    out = json.loads(render_json(FIXTURE))
    assert out == FIXTURE
