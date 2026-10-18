"""Industry grouping for the universe.

Symbols carry a fine-grained ``sector`` (``pharma``, ``cdmo``, ``ai_tech``,
``storage_hardware``, …). That granularity is right for screening but wrong for
navigation: an 87-symbol watchlist needs a handful of headings, not eleven.

Groups are derived from the sector rather than stored on the row. A stock's
sector is a fact about the company; the grouping is an editorial choice about
how to present it, and re-grouping should never need a migration or a backfill.

Where a company plausibly sits in two groups the rule is **what the market
trades it as**. Recursion is an AI company that does drug discovery, so it
trades on model results and compute partnerships — it goes in AI. Novo Nordisk
uses machine learning extensively and trades on trial data — it goes in pharma.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SectorGroup:
    """A navigation heading over one or more fine-grained sectors."""

    key: str
    label: str
    description: str
    sectors: tuple[str, ...]


GROUPS: tuple[SectorGroup, ...] = (
    SectorGroup(
        key="pharma_life_sciences",
        label="Pharma & Life Sciences",
        description=(
            "Drug developers and everyone who supplies them: large-cap pharma, "
            "biotech, contract manufacturers and research organisations, "
            "instruments, devices and health IT."
        ),
        sectors=(
            "pharma",
            "biotech",
            # Separate from "biotech" because the two trade on different
            # things. A company with an approved product trades on
            # prescriptions, pricing and guidance; a clinical-stage company has
            # no revenue to guide and trades almost entirely on scheduled
            # binary events — a readout, a PDUFA date, an advisory committee.
            # They are the names the catalyst calendar exists for, and mixing
            # them into "biotech" made a 40-name cohort invisible inside it.
            "clinical_stage",
            "cdmo",
            "cro",
            "life_science_tools",
            "medtech",
            "consumer_health",
            "health_it",
        ),
    ),
    SectorGroup(
        key="ai",
        label="Artificial Intelligence",
        description=(
            "Companies whose valuation moves on AI demand: model and platform "
            "owners, the semiconductors that train them, the broader chip "
            "industry they are cut from, the equipment that fabricates those, "
            "the networking that connects them, and AI-first drug discovery."
        ),
        sectors=(
            "ai_tech",
            "ai_semiconductor",
            # Separate from ai_semiconductor, which is defined by what moves
            # the valuation rather than by what the company makes. Analog, RF,
            # power and mature-node foundry names trade on car and handset
            # inventory, not on cluster build-out, and ranking them in the
            # same cohort would score two different cycles against each other.
            "semiconductor",
            "ai_equipment",
            "ai_networking",
            "ai_software",
            "ai_health",
        ),
    ),
    SectorGroup(
        key="data_storage",
        label="Data Storage & Infrastructure",
        description=(
            "Where the data physically lives and how it is queried: drives and "
            "memory, the servers and storage systems built from them, data "
            "platforms, the data centres themselves, and the power and cooling "
            "that limit how many of them get built."
        ),
        sectors=(
            "storage_hardware",
            # The machines the racks are filled with. Mostly Taiwanese ODMs,
            # who build for the hyperscalers rather than selling a brand, and
            # therefore trade on order flow rather than on end demand.
            "server_hardware",
            "memory",
            "cloud_storage",
            "data_platform",
            "data_center",
            # Power and cooling. Separate from data_center because a REIT
            # leasing floor space and a company selling switchgear respond to
            # the same demand through completely different mechanics — and
            # because power availability, not floor space, is the binding
            # constraint on the build-out.
            "datacenter_power",
        ),
    ),
)

# sector -> group key, built once. A sector in two groups would be a bug, so
# the loop asserts uniqueness rather than silently letting the last one win.
_SECTOR_TO_GROUP: dict[str, str] = {}
for _group in GROUPS:
    for _sector in _group.sectors:
        if _sector in _SECTOR_TO_GROUP:  # pragma: no cover - guards a typo
            raise ValueError(f"sector {_sector!r} is claimed by two groups")
        _SECTOR_TO_GROUP[_sector] = _group.key

OTHER = SectorGroup(
    key="other",
    label="Other",
    description="Symbols whose sector is not mapped to a group yet.",
    sectors=(),
)


def group_for(sector: str | None) -> str:
    """The group key for a sector. Unmapped sectors land in ``other``."""
    if not sector:
        return OTHER.key
    return _SECTOR_TO_GROUP.get(sector.strip().lower(), OTHER.key)


def sectors_in(group_key: str) -> tuple[str, ...]:
    """Every sector belonging to a group, for filtering a query."""
    for group in GROUPS:
        if group.key == group_key:
            return group.sectors
    return ()


def describe(group: SectorGroup) -> dict:
    """Serialisable view for the API."""
    return {
        "key": group.key,
        "label": group.label,
        "description": group.description,
        "sectors": list(group.sectors),
    }


def all_groups() -> tuple[SectorGroup, ...]:
    """Every group, including the catch-all, in display order."""
    return GROUPS + (OTHER,)


def is_known_sector(sector: str) -> bool:
    """Whether a sector is mapped to a group, for validating a filter.

    A filter on an unmapped sector matches nothing, and an empty result reads
    as "no symbols qualified" rather than "you spelled it wrong".
    """
    return sector.strip().lower() in _SECTOR_TO_GROUP
