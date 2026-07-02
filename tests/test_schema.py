import json
from pathlib import Path

from pharma_report.schema import REPORT_SCHEMA

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sample_digest.json").read_text())


def test_schema_top_level_shape():
    assert REPORT_SCHEMA["type"] == "object"
    assert set(REPORT_SCHEMA["required"]) == {"week_of", "generated_at", "ispe_events", "pharma_news"}
    assert REPORT_SCHEMA["additionalProperties"] is False


def test_schema_item_required_fields():
    event_required = set(REPORT_SCHEMA["properties"]["ispe_events"]["items"]["required"])
    assert event_required == {"chapter", "title", "date", "summary"}

    news_required = set(REPORT_SCHEMA["properties"]["pharma_news"]["items"]["required"])
    assert news_required == {"headline", "source", "date", "summary"}


def test_fixture_satisfies_required_fields():
    assert set(FIXTURE.keys()) >= set(REPORT_SCHEMA["required"])
    event_required = set(REPORT_SCHEMA["properties"]["ispe_events"]["items"]["required"])
    for event in FIXTURE["ispe_events"]:
        assert event_required <= set(event.keys())
    news_required = set(REPORT_SCHEMA["properties"]["pharma_news"]["items"]["required"])
    for item in FIXTURE["pharma_news"]:
        assert news_required <= set(item.keys())
