"""Builds and executes the single Claude API call that produces the weekly digest.

Uses the server-side ``web_search`` tool so no scraping/RSS code has to be
maintained here — Claude runs the searches itself and returns a JSON payload
that conforms to ``schema.REPORT_SCHEMA``. Swapping in a curated feed or a
dedicated news API later just means adding another data source to the prompt
(or a separate ``sources/`` module feeding into ``collect_digest``), not a
rewrite of this call.
"""

from __future__ import annotations

import json

import anthropic

from .config import Config
from .schema import REPORT_SCHEMA


def build_prompt(config: Config) -> str:
    chapters = "\n".join(f"- {c}" for c in config.ispe_chapters)
    sources = "\n".join(f"- {s}" for s in config.news_sources)
    return f"""Compile a weekly digest for a pharmaceutical / life sciences audience in Europe.

## Part 1 — ISPE regional chapter events
Search for events (conferences, training, chapter meetings, webinars) from the following
ISPE European regional chapters, focusing on events happening in the next {config.lookahead_days} days
(include an event that concluded in the last few days if it's notable and you can't find enough
upcoming ones):

{chapters}

Also check ISPE's central events/conference pages for anything Europe-tagged.

## Part 2 — Pharma & life sciences industry news
Search for notable pharma / life sciences industry news relevant to Europe from the past
{config.lookback_days} days — regulatory (EMA, national agencies), manufacturing, M&A, clinical
trials, supply chain, and major company announcements. Prioritize these outlets but use others
if they surface something significant:

{sources}

## Output
Return ONLY the structured JSON matching the provided schema. Deduplicate near-identical items.
Skip items you can't find a real, verifiable source URL for. Write concise summaries (1-2 sentences).
If a section genuinely has no qualifying items this week, return an empty list for it rather than
inventing content.
"""


def fetch_digest(config: Config) -> dict:
    if not config.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    with client.messages.stream(
        model=config.model,
        max_tokens=16000,
        tools=[
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": config.web_search_max_uses,
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": REPORT_SCHEMA}},
        messages=[{"role": "user", "content": build_prompt(config)}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise RuntimeError("Claude declined the request (stop_reason=refusal)")

    text_block = next((b for b in message.content if b.type == "text"), None)
    if text_block is None:
        raise RuntimeError(f"No text block in response; stop_reason={message.stop_reason}")

    return json.loads(text_block.text)
