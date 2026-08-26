# How this platform compares

**Read this with two caveats.** My knowledge of commercial products has a
training cutoff, and this sandbox's network policy blocks the vendor sites, so
I could not verify anyone's current feature set or pricing while writing it.
Treat the competitor columns as a map of *categories* to check, not as today's
fact sheet. What is stated about **this** platform is verified against the code
and the test suite.

## The honest positioning

This is not a Bloomberg competitor and should not try to be. It is a **narrow,
self-hosted signal tool**: it watches a pharma/life-sciences universe, scores
news for sentiment and business event type, alerts on what matters, and lets
you test whether the signal actually predicted anything.

Its real advantages are ones the large platforms structurally cannot offer:

- **The scoring is yours and auditable.** `LexiconAnalyzer.explain()` shows
  every term that fired and its weight. Commercial sentiment scores are opaque
  numbers you must take on trust.
- **Domain-tuned rather than general.** The lexicon knows what a complete
  response letter, a CHMP positive opinion and a Form 483 mean. General
  financial NLP treats them as ordinary words.
- **You own the data.** Your alerts, portfolios and history live in your
  database, exportable, with no per-seat licence and no vendor lock-in.
- **Backtesting is built in.** Most news terminals show you sentiment; few let
  you ask "did this signal actually move the price?" on your own history.

## Where it stands against the field

| Capability | This platform | Typical premium terminal | Typical retail/prosumer tool |
|---|---|---|---|
| Real-time prices | Finnhub trade stream, demand-driven subscriptions | Full depth, direct exchange feeds | Delayed or consolidated feed |
| News coverage | SEC EDGAR + Finnhub company news | Dozens of wires, exclusive sources, transcripts | Aggregated web/RSS |
| Sentiment | Transparent domain lexicon (or FinBERT) | Proprietary, opaque, broad-market | Often none, or a crude score |
| Event taxonomy | 12 pharma-relevant types | Extensive, general-purpose | Rare |
| Alerting | In-app, Slack, email | Every channel, highly configurable | Email/push |
| Backtesting news impact | Yes, per event type | Usually a separate product | Rare |
| Screening | Region/country/venue/currency/sector/text | Hundreds of fundamental fields | Dozens of fields |
| Fundamentals | **None** | Comprehensive | Moderate |
| Analyst estimates | **None** | Comprehensive | Some |
| Filing full text | **Metadata only** | Full text + search | Varies |
| Cost | Infrastructure + data API fees | Very high per seat | Low to moderate |

## The gaps that matter most

Ranked by how much they limit the product today:

1. **No valuation.** Earnings surprise and analyst revisions are now a
   weighted pillar of the score, and market cap is stored — but nothing here
   tells you whether a stock is *expensive*. There are no multiples, no
   estimates beyond the next quarter's consensus EPS, and no cash-flow data.
   Coverage is also uneven: the vendor's free tier is US-only, so roughly half
   the universe has no fundamental input at all and is scored on the remaining
   pillars with `coverage` reporting the shortfall.
2. **Filing metadata, not filing text.** The platform reads *that* an 8-K was
   filed and which items it reported, not what it said. Fetching the primary
   document and scoring its text is the single biggest signal upgrade
   available, and needs no new vendor.
3. **No FX conversion.** With a multi-region universe a portfolio can hold JPY,
   EUR and USD lines. The valuation reports a per-currency breakdown and flags
   `mixed_currency` rather than presenting a meaningless total — honest, but a
   real limitation.
4. **No corporate actions.** Splits and dividends are not adjusted for, so a
   split shows up as a price crash in the backtester.
5. **Single-instance by design.** The scheduler must not run twice and the
   WebSocket hub keeps subscriber state in memory. Scaling out needs Redis.
6. **No holiday calendars.** Market sessions are weekday-and-clock only, so a
   public holiday reads as an open market with no trades.

## Multi-region coverage

The universe now spans three regions, resolved from the vendor symbol suffix
(`app/services/markets.py`):

| Region | Venues covered | Example symbols |
|---|---|---|
| North America | US, Toronto, TSX Venture, Cboe Canada, Mexico | `PFE`, `SHOP.TO`, `WALMEX.MX` |
| Europe | London, Euronext (Paris/Amsterdam/Brussels/Lisbon/Dublin), XETRA, Frankfurt, SIX, Milan, Madrid, Stockholm, Copenhagen, Helsinki, Oslo, Vienna, Warsaw, Athens | `AZN.L`, `SAN.PA`, `ROG.SW`, `NOVO-B.CO` |
| Asia-Pacific | Tokyo, Hong Kong, Shanghai, Shenzhen, Korea, KOSDAQ, Taiwan, Singapore, India (NSE/BSE), Australia, New Zealand, Thailand, Indonesia, Malaysia | `4502.T`, `2269.HK`, `207940.KS`, `CSL.AX` |

Two regional details are encoded deliberately because they cause silent errors:

- **London quotes in pence**, so its currency code is `GBp` and
  `markets.normalise_price()` divides by 100. Without this a London price reads
  100x too high against a US cross-listing.
- **Sessions do not overlap.** Tokyo closes before New York opens. A quiet
  price stream at 09:00 UTC is a closed market, not a broken feed —
  `GET /stocks/markets` shows which venues are open right now.

### One caveat on non-US coverage

**SEC EDGAR only covers US registrants.** A European or Asian company without a
US listing files with its home regulator, not the SEC, so for those names the
platform depends entirely on Finnhub news. Adding RNS (UK), the EU's OAM
network, TDnet (Japan) or HKEX filings would close that gap; none is wired up
today.

This is also why the seed universe carries both the ADR and the home line for
the big names — `NVO` and `NOVO-B.CO`, `AZN` and `AZN.L`. The ADR brings SEC
filings and US-hours liquidity; the home line brings the domestic session and
local currency.

## Against Danelfin specifically

Danelfin is the closest comparison to what this platform now does: an AI score
per stock, decomposed into pillars, with the features that drove it shown, and
a published track record. Same caveat as everywhere else on this page — I could
not reach their site while writing this, so treat the left column as a
description of the *category* rather than a current fact sheet.

| | Danelfin (as I understand it) | This platform |
|---|---|---|
| Score | AI Score 1–10, stated as probability of beating the market over ~3 months | 0–100 percentile rank. **No probability claim** |
| Pillars | Fundamental, technical, sentiment | Technical, sentiment, fundamental — the last weighted only after it measured, and US-listed coverage only |
| Features | ~900 per stock per day, machine-learned weights | 9 named factors, hand-set weights, arithmetic shown in full |
| Explainability | Top features driving the score | Every factor, its raw value, percentile, weight and contribution |
| Track record | Published, multi-year, third-party visible | `GET /scores/validation` measured on *your* data: several start dates, each pillar separately, reported with its caveats |
| Universe | ~1,000 US + ~600 European | 163, whatever you seed |
| Coverage honesty | — | `coverage` field states what share of intended inputs each score used |
| Cost | Subscription | Infrastructure + data fees |

**Where Danelfin is straightforwardly better.** Fundamentals are a whole
pillar this has nothing for. Machine-learned weights across hundreds of
features will capture interactions that nine hand-weighted factors cannot. A
published multi-year track record is worth far more than a single-period test
on one user's database. Their universe is an order of magnitude larger.

**Where this is better, and it is not nothing.** The score is fully auditable
— not "here are the top features" but the entire calculation, reproducible by
hand from the response. It is tuned to a sector: the lexicon knows what a
complete response letter and a CHMP opinion are. And it makes no forecast it
cannot support: a 1–10 score presented as a probability is a calibration claim,
and calibration is the hardest thing in this field to get right and the easiest
to assert.

**What the validation actually said on first use.** Over one 21-day window
from 2026-07-11 the top quintile returned −6.4% against the bottom quintile's
−2.6%: the ranking was *inverted*, and every quintile was negative. That is
one falling month, and a trend-following score is exactly what suffers in a
reversal — but a single period cannot distinguish "the score is wrong" from
"that month went against it", which is why the endpoint now tests several
start dates and reports each pillar separately. Take the number the endpoint
gives you over anything claimed here.

**The honest summary.** If you want a researched, validated score across a
large universe, buy one. What this gives you is a score you can take apart,
over a universe you chose, on data you own — and a validation endpoint that
will tell you when it is not working.

## What I would do next, in order

1. **Fetch and score SEC filing text**, not just item codes — biggest signal
   gain, no new vendor.
2. **Valuation multiples** — earnings surprise and revisions are in and
   weighted; what is expensive versus cheap still is not.
3. **Corporate actions** so the backtester stops reading splits as crashes.
4. **FX rates** so multi-region portfolios can show one trustworthy total.
5. **Exchange holiday calendars** so "market open" is true rather than
   approximate.
