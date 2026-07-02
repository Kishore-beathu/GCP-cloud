"""JSON schema for the structured weekly digest returned by Claude."""

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "week_of": {
            "type": "string",
            "description": "ISO date (YYYY-MM-DD) for the Monday of the reporting week.",
        },
        "generated_at": {
            "type": "string",
            "description": "ISO 8601 timestamp when this digest was generated.",
        },
        "ispe_events": {
            "type": "array",
            "description": "Upcoming or recently concluded events from ISPE European regional chapters.",
            "items": {
                "type": "object",
                "properties": {
                    "chapter": {"type": "string", "description": "e.g. 'ISPE DACH', 'ISPE France'"},
                    "title": {"type": "string"},
                    "date": {"type": "string", "description": "Event date or date range, human readable."},
                    "location": {"type": "string"},
                    "url": {"type": "string"},
                    "summary": {"type": "string", "description": "One to two sentence summary."},
                },
                "required": ["chapter", "title", "date", "summary"],
                "additionalProperties": False,
            },
        },
        "pharma_news": {
            "type": "array",
            "description": "Notable pharma / life sciences industry news in Europe from the past week.",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "source": {"type": "string", "description": "Publication or organization name."},
                    "date": {"type": "string", "description": "Publication date, human readable."},
                    "url": {"type": "string"},
                    "summary": {"type": "string", "description": "One to two sentence summary."},
                },
                "required": ["headline", "source", "date", "summary"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["week_of", "generated_at", "ispe_events", "pharma_news"],
    "additionalProperties": False,
}
