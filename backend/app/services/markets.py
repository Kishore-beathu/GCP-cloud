"""Exchange, region and currency metadata for a multi-region universe.

Data vendors identify a listing by a suffixed symbol — ``AZN.L``,
``NOVO-B.CO``, ``7203.T``, ``SHOP.TO``. The suffix is the only thing that says
which venue, country, currency and trading calendar a symbol belongs to, so it
is resolved once here and stored on the row rather than re-derived at every
call site.

Two details bite people repeatedly and are encoded deliberately:

* **London quotes in pence, not pounds** (``GBp``). A London price is 100x a
  naive reading, which silently wrecks any cross-listing comparison.
* **Asian and European sessions do not overlap with New York.** A quiet live
  price stream at 09:00 UTC is Tokyo being closed, not a broken feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# Regions are the coarse grouping a user actually filters on.
REGIONS = ("north_america", "europe", "asia_pacific")


@dataclass(frozen=True)
class Market:
    """One trading venue."""

    suffix: str          # vendor symbol suffix; "" for US listings
    name: str
    mic: str             # ISO 10383 market identifier code
    country: str         # ISO 3166-1 alpha-2
    region: str
    currency: str        # ISO 4217, or GBp for London's pence quotes
    timezone: str
    opens: time
    closes: time

    @property
    def quotes_in_minor_units(self) -> bool:
        """London prices arrive in pence; dividing by 100 gives pounds."""
        return self.currency == "GBp"

    def is_open(self, moment: datetime | None = None) -> bool:
        """Rough session check — weekday and clock only, no holiday calendar."""
        moment = (moment or datetime.now(timezone.utc)).astimezone(ZoneInfo(self.timezone))
        if moment.weekday() >= 5:
            return False
        return self.opens <= moment.time() <= self.closes


def _market(
    suffix: str, name: str, mic: str, country: str, region: str,
    currency: str, tz: str, opens: str, closes: str,
) -> Market:
    return Market(
        suffix=suffix, name=name, mic=mic, country=country, region=region,
        currency=currency, timezone=tz,
        opens=time.fromisoformat(opens), closes=time.fromisoformat(closes),
    )


MARKETS: tuple[Market, ...] = (
    # --- North America ------------------------------------------------------
    _market("", "US (NYSE/Nasdaq)", "XNYS", "US", "north_america", "USD",
            "America/New_York", "09:30", "16:00"),
    _market(".TO", "Toronto Stock Exchange", "XTSE", "CA", "north_america", "CAD",
            "America/Toronto", "09:30", "16:00"),
    _market(".V", "TSX Venture Exchange", "XTSX", "CA", "north_america", "CAD",
            "America/Toronto", "09:30", "16:00"),
    _market(".NE", "Cboe Canada", "NEOE", "CA", "north_america", "CAD",
            "America/Toronto", "09:30", "16:00"),
    _market(".MX", "Bolsa Mexicana de Valores", "XMEX", "MX", "north_america", "MXN",
            "America/Mexico_City", "08:30", "15:00"),
    # --- Europe -------------------------------------------------------------
    # London quotes in pence — see Market.quotes_in_minor_units.
    _market(".L", "London Stock Exchange", "XLON", "GB", "europe", "GBp",
            "Europe/London", "08:00", "16:30"),
    _market(".PA", "Euronext Paris", "XPAR", "FR", "europe", "EUR",
            "Europe/Paris", "09:00", "17:30"),
    _market(".AS", "Euronext Amsterdam", "XAMS", "NL", "europe", "EUR",
            "Europe/Amsterdam", "09:00", "17:30"),
    _market(".BR", "Euronext Brussels", "XBRU", "BE", "europe", "EUR",
            "Europe/Brussels", "09:00", "17:30"),
    _market(".LS", "Euronext Lisbon", "XLIS", "PT", "europe", "EUR",
            "Europe/Lisbon", "08:00", "16:30"),
    _market(".IR", "Euronext Dublin", "XDUB", "IE", "europe", "EUR",
            "Europe/Dublin", "08:00", "16:30"),
    _market(".DE", "XETRA", "XETR", "DE", "europe", "EUR",
            "Europe/Berlin", "09:00", "17:30"),
    _market(".F", "Frankfurt Stock Exchange", "XFRA", "DE", "europe", "EUR",
            "Europe/Berlin", "08:00", "20:00"),
    _market(".SW", "SIX Swiss Exchange", "XSWX", "CH", "europe", "CHF",
            "Europe/Zurich", "09:00", "17:30"),
    _market(".MI", "Borsa Italiana", "XMIL", "IT", "europe", "EUR",
            "Europe/Rome", "09:00", "17:30"),
    _market(".MC", "Bolsa de Madrid", "XMAD", "ES", "europe", "EUR",
            "Europe/Madrid", "09:00", "17:30"),
    _market(".ST", "Nasdaq Stockholm", "XSTO", "SE", "europe", "SEK",
            "Europe/Stockholm", "09:00", "17:30"),
    _market(".CO", "Nasdaq Copenhagen", "XCSE", "DK", "europe", "DKK",
            "Europe/Copenhagen", "09:00", "17:00"),
    _market(".HE", "Nasdaq Helsinki", "XHEL", "FI", "europe", "EUR",
            "Europe/Helsinki", "10:00", "18:30"),
    _market(".OL", "Oslo Børs", "XOSL", "NO", "europe", "NOK",
            "Europe/Oslo", "09:00", "16:20"),
    _market(".VI", "Wiener Börse", "XWBO", "AT", "europe", "EUR",
            "Europe/Vienna", "09:00", "17:30"),
    _market(".WA", "Warsaw Stock Exchange", "XWAR", "PL", "europe", "PLN",
            "Europe/Warsaw", "09:00", "17:00"),
    _market(".AT", "Athens Stock Exchange", "XATH", "GR", "europe", "EUR",
            "Europe/Athens", "10:15", "17:20"),
    # --- Asia-Pacific -------------------------------------------------------
    _market(".T", "Tokyo Stock Exchange", "XTKS", "JP", "asia_pacific", "JPY",
            "Asia/Tokyo", "09:00", "15:30"),
    _market(".HK", "Hong Kong Stock Exchange", "XHKG", "HK", "asia_pacific", "HKD",
            "Asia/Hong_Kong", "09:30", "16:00"),
    _market(".SS", "Shanghai Stock Exchange", "XSHG", "CN", "asia_pacific", "CNY",
            "Asia/Shanghai", "09:30", "15:00"),
    _market(".SZ", "Shenzhen Stock Exchange", "XSHE", "CN", "asia_pacific", "CNY",
            "Asia/Shanghai", "09:30", "15:00"),
    _market(".KS", "Korea Exchange", "XKRX", "KR", "asia_pacific", "KRW",
            "Asia/Seoul", "09:00", "15:30"),
    _market(".KQ", "KOSDAQ", "XKOS", "KR", "asia_pacific", "KRW",
            "Asia/Seoul", "09:00", "15:30"),
    _market(".TW", "Taiwan Stock Exchange", "XTAI", "TW", "asia_pacific", "TWD",
            "Asia/Taipei", "09:00", "13:30"),
    _market(".SI", "Singapore Exchange", "XSES", "SG", "asia_pacific", "SGD",
            "Asia/Singapore", "09:00", "17:00"),
    _market(".NS", "National Stock Exchange of India", "XNSE", "IN", "asia_pacific", "INR",
            "Asia/Kolkata", "09:15", "15:30"),
    _market(".BO", "BSE India", "XBOM", "IN", "asia_pacific", "INR",
            "Asia/Kolkata", "09:15", "15:30"),
    _market(".AX", "Australian Securities Exchange", "XASX", "AU", "asia_pacific", "AUD",
            "Australia/Sydney", "10:00", "16:00"),
    _market(".NZ", "NZX", "XNZE", "NZ", "asia_pacific", "NZD",
            "Pacific/Auckland", "10:00", "16:45"),
    _market(".BK", "Stock Exchange of Thailand", "XBKK", "TH", "asia_pacific", "THB",
            "Asia/Bangkok", "10:00", "16:30"),
    _market(".JK", "Indonesia Stock Exchange", "XIDX", "ID", "asia_pacific", "IDR",
            "Asia/Jakarta", "09:00", "15:50"),
    _market(".KL", "Bursa Malaysia", "XKLS", "MY", "asia_pacific", "MYR",
            "Asia/Kuala_Lumpur", "09:00", "17:00"),
)

US_MARKET = MARKETS[0]

_BY_SUFFIX = {market.suffix: market for market in MARKETS if market.suffix}
_BY_MIC = {market.mic: market for market in MARKETS}

# Longest suffixes first so ".TWO" cannot be shadowed by ".TW".
_SUFFIXES_LONGEST_FIRST = tuple(sorted(_BY_SUFFIX, key=len, reverse=True))


def resolve(ticker: str) -> Market:
    """Map a vendor symbol to its market. Unsuffixed symbols are treated as US."""
    symbol = ticker.strip().upper()
    for suffix in _SUFFIXES_LONGEST_FIRST:
        if symbol.endswith(suffix):
            return _BY_SUFFIX[suffix]
    return US_MARKET


def by_mic(mic: str) -> Market | None:
    return _BY_MIC.get(mic.strip().upper())


def markets_in(region: str) -> tuple[Market, ...]:
    return tuple(market for market in MARKETS if market.region == region)


def open_markets(moment: datetime | None = None) -> tuple[Market, ...]:
    return tuple(market for market in MARKETS if market.is_open(moment))


def normalise_price(price: float, market: Market) -> float:
    """Convert a quote to the currency's major unit (pence -> pounds)."""
    return price / 100 if market.quotes_in_minor_units else price


def describe(market: Market) -> dict:
    """Serialisable view for the API."""
    return {
        "suffix": market.suffix,
        "name": market.name,
        "mic": market.mic,
        "country": market.country,
        "region": market.region,
        "currency": market.currency,
        "timezone": market.timezone,
        "opens": market.opens.isoformat(timespec="minutes"),
        "closes": market.closes.isoformat(timespec="minutes"),
        "is_open": market.is_open(),
    }


def next_open(market: Market, moment: datetime | None = None) -> datetime:
    """When this venue next opens, in UTC.

    Weekday and clock only, like `is_open` — there is no holiday calendar
    here, so this can name a session that a public holiday will cancel. It is
    used to say "the window opens in three hours", which is useful when
    approximate and misleading only if presented as certain.
    """
    zone = ZoneInfo(market.timezone)
    local = (moment or datetime.now(timezone.utc)).astimezone(zone)

    candidate = local.replace(
        hour=market.opens.hour, minute=market.opens.minute, second=0, microsecond=0
    )
    if candidate <= local:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def next_close(market: Market, moment: datetime | None = None) -> datetime:
    """When the current or next session ends, in UTC."""
    zone = ZoneInfo(market.timezone)
    local = (moment or datetime.now(timezone.utc)).astimezone(zone)

    candidate = local.replace(
        hour=market.closes.hour, minute=market.closes.minute, second=0, microsecond=0
    )
    if candidate <= local:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def session_state(market: Market, tz: str = "UTC", moment: datetime | None = None) -> dict:
    """Whether a venue is trading, and when that next changes.

    Reported in a requested timezone as well as UTC, because "the window is
    15:30-22:00" is only true in one place and the reader is rarely in the
    exchange's own zone.
    """
    now = moment or datetime.now(timezone.utc)
    zone = ZoneInfo(tz)
    open_now = market.is_open(now)
    changes_at = next_close(market, now) if open_now else next_open(market, now)

    return {
        "venue": market.name,
        "is_open": open_now,
        "session_local": f"{market.opens:%H:%M}-{market.closes:%H:%M} {market.timezone}",
        "session_in_tz": _session_in_tz(market, zone, now),
        "next_change_utc": changes_at.isoformat(),
        "next_change_local": changes_at.astimezone(zone).isoformat(),
        "minutes_until_change": round((changes_at - now).total_seconds() / 60),
        "timezone": tz,
    }


def _session_in_tz(market: Market, zone: ZoneInfo, now: datetime) -> str:
    """The venue's opening hours rendered in another zone.

    Computed from a real date rather than by adding a fixed offset, so it
    follows daylight saving on both sides instead of drifting by an hour for
    several weeks a year.
    """
    venue_zone = ZoneInfo(market.timezone)
    today = now.astimezone(venue_zone).date()
    opens = datetime.combine(today, market.opens, tzinfo=venue_zone).astimezone(zone)
    closes = datetime.combine(today, market.closes, tzinfo=venue_zone).astimezone(zone)
    return f"{opens:%H:%M}-{closes:%H:%M}"
