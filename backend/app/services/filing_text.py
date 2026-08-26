"""The narrative of an 8-K, not just its item codes.

The platform knows *that* an 8-K was filed and which items it reported. It does
not know what the filing said, which is the largest untapped signal in the
stack: the item code says "Other Events" and the document says the trial met
its endpoint.

**Item 8.01 only, on purpose.** It is where most material announcements that
have no dedicated item land, and proving extraction on one item type is worth
more than half-working on nine. Filings are long, formulaic and adversarially
drafted, and a lexicon tuned on headlines does not automatically transfer to
them — so this stores the text and lets the existing scorer read it, rather
than claiming a filing-specific model it has not earned.

What this does *not* do is parse meaning. It strips the document to readable
prose, cuts the boilerplate that every 8-K carries, and hands the result to the
scorer as an article body. If that turns out to score badly, the fix is a
labelled corpus of filing text — a project, not a parameter.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# Item 8.01 is "Other Events" — the catch-all that material news falls into
# when no specific item covers it.
TARGET_ITEM = "8.01"

# Long enough to carry a sentence worth scoring, short enough to stay inside
# the sentiment backend's input limit without truncating mid-argument.
MAX_BODY_CHARS = 4000


# Boilerplate that appears in essentially every 8-K and would otherwise
# dominate a short filing's score. "Forward-looking statements" alone carries
# risk, uncertainty, delay and failure — four negative terms, in a paragraph
# that is identical across every filing and therefore says nothing about any
# of them.
_BOILERPLATE_MARKERS = (
    "forward-looking statements",
    "forward looking statements",
    "safe harbor",
    "safe harbour",
    "pursuant to the requirements of the securities exchange act",
    "signatures",
    "exhibit index",
)


class _Stripper(HTMLParser):
    """HTML to text, keeping the block structure that separates sentences.

    EDGAR documents are HTML tables and inline styling wrapped around a few
    paragraphs of prose. Dropping tags without inserting breaks runs the last
    word of one paragraph into the first of the next, which is how "the
    endpoint was met" and "Risks include failure" become one sentence.
    """

    _BLOCKS = frozenset(
        {"p", "div", "br", "tr", "td", "th", "li", "h1", "h2", "h3", "h4", "table"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def to_text(document: str) -> str:
    """Readable prose from an EDGAR HTML or plain-text filing."""
    if "<" in document and ">" in document:
        stripper = _Stripper()
        try:
            stripper.feed(document)
        except Exception:  # noqa: BLE001 - malformed filings are common
            logger.debug("Filing HTML did not parse cleanly; falling back to raw text")
            document = re.sub(r"<[^>]+>", " ", document)
        else:
            document = stripper.text()

    # Non-breaking spaces are everywhere in EDGAR output and are not \s.
    document = document.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in document.splitlines()]
    return "\n".join(line for line in lines if line)


def strip_boilerplate(text: str) -> str:
    """Cut the legal furniture every 8-K carries.

    The forward-looking-statements paragraph alone contains risk, uncertainty,
    delay and failure — four negative terms, in wording identical across every
    filing ever made, which would drag a short announcement negative on
    language that says nothing about it.

    Cut unconditionally, with no minimum content before the marker. Two
    attempts at such a floor both silently stopped firing on short filings —
    at two hundred characters and then at forty — and short filings are
    exactly where a boilerplate paragraph does the most damage, because there
    is least real text to outweigh it. A section that was *only* boilerplate
    now cuts to nothing, and `prepare` rejects it for being too short, which is
    the right answer: a safe-harbour notice with no announcement attached is
    not an announcement.
    """
    lowered = text.lower()
    cut = len(text)
    for marker in _BOILERPLATE_MARKERS:
        found = lowered.find(marker)
        if found >= 0:
            cut = min(cut, found)
    return text[:cut].strip()


def extract_item(text: str, item: str = TARGET_ITEM) -> str | None:
    """The narrative under one item heading.

    An 8-K reporting items 8.01 and 9.01 is mostly the exhibit list, and
    scoring the whole document would read a press-release title in an exhibit
    index as the announcement itself.
    """
    pattern = re.compile(
        rf"item\s+{re.escape(item)}[.:\s—-]*(.*?)(?=\bitem\s+\d+\.\d+\b|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None
    body = match.group(1).strip()
    # The heading's own title ("Other Events") is not content.
    body = re.sub(r"^other\s+events[.:\s]*", "", body, flags=re.IGNORECASE).strip()
    return body or None


def prepare(document: str, item: str = TARGET_ITEM) -> str | None:
    """Filing HTML to a scoreable body, or None if there is nothing to score."""
    text = to_text(document)
    if not text:
        return None
    section = extract_item(text, item)
    if section is None:
        return None
    section = strip_boilerplate(section)
    if len(section) < 80:
        # An item that says only "see the attached press release" is a pointer,
        # not an announcement. Scoring it would add a neutral row and a little
        # false confidence to the volume factor.
        return None
    return section[:MAX_BODY_CHARS]
