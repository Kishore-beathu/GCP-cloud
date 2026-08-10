"""The tracked ticker universe.

Week 1 ships a curated pharma/life-sciences seed list. Week 2 expands this
toward the full ~1000-symbol universe by loading ``data/tickers.csv`` when
present, so growing the watchlist never requires a code change.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Stock
from app.services import markets

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "tickers.csv"


@dataclass(frozen=True)
class TickerSeed:
    ticker: str
    company_name: str
    sector: str
    exchange: str | None = None


SEED_TICKERS: tuple[TickerSeed, ...] = (
    # Large-cap pharma
    TickerSeed("PFE", "Pfizer Inc.", "pharma", "NYSE"),
    TickerSeed("MRK", "Merck & Co. Inc.", "pharma", "NYSE"),
    TickerSeed("JNJ", "Johnson & Johnson", "pharma", "NYSE"),
    TickerSeed("LLY", "Eli Lilly and Company", "pharma", "NYSE"),
    TickerSeed("ABBV", "AbbVie Inc.", "pharma", "NYSE"),
    TickerSeed("BMY", "Bristol-Myers Squibb", "pharma", "NYSE"),
    TickerSeed("AZN", "AstraZeneca PLC", "pharma", "NASDAQ"),
    TickerSeed("NVS", "Novartis AG", "pharma", "NYSE"),
    TickerSeed("GSK", "GSK plc", "pharma", "NYSE"),
    TickerSeed("SNY", "Sanofi", "pharma", "NASDAQ"),
    TickerSeed("NVO", "Novo Nordisk A/S", "pharma", "NYSE"),
    TickerSeed("TAK", "Takeda Pharmaceutical", "pharma", "NYSE"),
    # Biotech
    TickerSeed("MRNA", "Moderna Inc.", "biotech", "NASDAQ"),
    TickerSeed("BNTX", "BioNTech SE", "biotech", "NASDAQ"),
    TickerSeed("REGN", "Regeneron Pharmaceuticals", "biotech", "NASDAQ"),
    TickerSeed("VRTX", "Vertex Pharmaceuticals", "biotech", "NASDAQ"),
    TickerSeed("AMGN", "Amgen Inc.", "biotech", "NASDAQ"),
    TickerSeed("GILD", "Gilead Sciences Inc.", "biotech", "NASDAQ"),
    TickerSeed("BIIB", "Biogen Inc.", "biotech", "NASDAQ"),
    TickerSeed("ALNY", "Alnylam Pharmaceuticals", "biotech", "NASDAQ"),
    TickerSeed("INCY", "Incyte Corporation", "biotech", "NASDAQ"),
    TickerSeed("SRPT", "Sarepta Therapeutics", "biotech", "NASDAQ"),
    TickerSeed("BMRN", "BioMarin Pharmaceutical", "biotech", "NASDAQ"),
    TickerSeed("GMAB", "Genmab A/S", "biotech", "NASDAQ"),
    TickerSeed("ARGX", "argenx SE", "biotech", "NASDAQ"),
    # CDMO / manufacturing / life-science tools
    TickerSeed("TMO", "Thermo Fisher Scientific", "life_science_tools", "NYSE"),
    TickerSeed("DHR", "Danaher Corporation", "life_science_tools", "NYSE"),
    TickerSeed("A", "Agilent Technologies", "life_science_tools", "NYSE"),
    TickerSeed("WAT", "Waters Corporation", "life_science_tools", "NYSE"),
    TickerSeed("RVTY", "Revvity Inc.", "life_science_tools", "NYSE"),
    TickerSeed("WST", "West Pharmaceutical Services", "cdmo", "NYSE"),
    TickerSeed("CRL", "Charles River Laboratories", "cdmo", "NYSE"),
    TickerSeed("IQV", "IQVIA Holdings Inc.", "cro", "NYSE"),
    TickerSeed("ICLR", "ICON plc", "cro", "NASDAQ"),
    TickerSeed("MEDP", "Medpace Holdings", "cro", "NASDAQ"),
    # --- Artificial intelligence ---------------------------------------------
    # Platform and model owners.
    TickerSeed("NVDA", "NVIDIA Corporation", "ai_semiconductor", "NASDAQ"),
    TickerSeed("MSFT", "Microsoft Corporation", "ai_tech", "NASDAQ"),
    TickerSeed("GOOGL", "Alphabet Inc.", "ai_tech", "NASDAQ"),
    TickerSeed("AMZN", "Amazon.com Inc.", "ai_tech", "NASDAQ"),
    TickerSeed("META", "Meta Platforms Inc.", "ai_tech", "NASDAQ"),
    TickerSeed("PLTR", "Palantir Technologies", "ai_software", "NASDAQ"),
    TickerSeed("NOW", "ServiceNow Inc.", "ai_software", "NYSE"),
    TickerSeed("CRM", "Salesforce Inc.", "ai_software", "NYSE"),
    # The silicon the models train and run on. TSM fabricates most of it and
    # ASML is the sole supplier of the EUV machines that make it possible, so
    # both trade on AI capital expenditure rather than on their own end markets.
    TickerSeed("AMD", "Advanced Micro Devices", "ai_semiconductor", "NASDAQ"),
    TickerSeed("AVGO", "Broadcom Inc.", "ai_semiconductor", "NASDAQ"),
    TickerSeed("TSM", "Taiwan Semiconductor (ADR)", "ai_semiconductor", "NYSE"),
    TickerSeed("ASML", "ASML Holding (ADR)", "ai_semiconductor", "NASDAQ"),
    TickerSeed("ARM", "Arm Holdings (ADR)", "ai_semiconductor", "NASDAQ"),
    TickerSeed("MRVL", "Marvell Technology", "ai_semiconductor", "NASDAQ"),
    TickerSeed("ASML.AS", "ASML Holding NV", "ai_semiconductor", "Euronext Amsterdam"),
    TickerSeed("2330.TW", "Taiwan Semiconductor Manufacturing", "ai_semiconductor", "TWSE"),
    # AI-first drug discovery: these trade on model results and compute
    # partnerships rather than on a marketed product, so they group with AI.
    TickerSeed("TEM", "Tempus AI Inc.", "ai_health", "NASDAQ"),
    TickerSeed("SDGR", "Schrodinger Inc.", "ai_health", "NASDAQ"),
    TickerSeed("RXRX", "Recursion Pharmaceuticals", "ai_health", "NASDAQ"),
    TickerSeed("ABSI", "Absci Corporation", "ai_health", "NASDAQ"),
    TickerSeed("CRSP", "CRISPR Therapeutics AG", "biotech", "NASDAQ"),
    # --- Data storage and infrastructure --------------------------------------
    # Drives and memory: the physical layer.
    TickerSeed("WDC", "Western Digital Corporation", "storage_hardware", "NASDAQ"),
    TickerSeed("STX", "Seagate Technology Holdings", "storage_hardware", "NASDAQ"),
    TickerSeed("NTAP", "NetApp Inc.", "storage_hardware", "NASDAQ"),
    TickerSeed("PSTG", "Pure Storage Inc.", "storage_hardware", "NYSE"),
    TickerSeed("DELL", "Dell Technologies Inc.", "storage_hardware", "NYSE"),
    TickerSeed("HPE", "Hewlett Packard Enterprise", "storage_hardware", "NYSE"),
    TickerSeed("MU", "Micron Technology Inc.", "memory", "NASDAQ"),
    TickerSeed("000660.KS", "SK hynix Inc.", "memory", "KRX"),
    TickerSeed("2408.TW", "Nanya Technology Corporation", "memory", "TWSE"),
    # Data platforms: where the data is queried rather than kept.
    TickerSeed("SNOW", "Snowflake Inc.", "data_platform", "NYSE"),
    TickerSeed("MDB", "MongoDB Inc.", "data_platform", "NASDAQ"),
    TickerSeed("CFLT", "Confluent Inc.", "data_platform", "NASDAQ"),
    TickerSeed("ORCL", "Oracle Corporation", "data_platform", "NYSE"),
    TickerSeed("TDC", "Teradata Corporation", "data_platform", "NYSE"),
    # The buildings. REITs, so they trade on leasing and power, not on chips.
    TickerSeed("EQIX", "Equinix Inc.", "data_center", "NASDAQ"),
    TickerSeed("DLR", "Digital Realty Trust", "data_center", "NYSE"),
    TickerSeed("VEEV", "Veeva Systems Inc.", "health_it", "NYSE"),
    TickerSeed("DXCM", "DexCom Inc.", "medtech", "NASDAQ"),
    TickerSeed("ISRG", "Intuitive Surgical", "medtech", "NASDAQ"),
    # --- Europe: primary listings, not the US depositary receipts -----------
    # ADRs (AZN, NVS, NVO above) trade US hours in USD; the home lines below
    # carry the local session, local currency and the domestic news flow.
    TickerSeed("AZN.L", "AstraZeneca PLC", "pharma"),
    TickerSeed("GSK.L", "GSK plc", "pharma"),
    TickerSeed("HLN.L", "Haleon plc", "consumer_health"),
    TickerSeed("NOVN.SW", "Novartis AG", "pharma"),
    TickerSeed("ROG.SW", "Roche Holding AG", "pharma"),
    TickerSeed("LONN.SW", "Lonza Group AG", "cdmo"),
    TickerSeed("NOVO-B.CO", "Novo Nordisk A/S", "pharma"),
    TickerSeed("GMAB.CO", "Genmab A/S", "biotech"),
    TickerSeed("SAN.PA", "Sanofi SA", "pharma"),
    TickerSeed("EL.PA", "EssilorLuxottica SA", "medtech"),
    TickerSeed("BAYN.DE", "Bayer AG", "pharma"),
    TickerSeed("MRK.DE", "Merck KGaA", "pharma"),
    TickerSeed("SHL.DE", "Siemens Healthineers AG", "medtech"),
    TickerSeed("SRT3.DE", "Sartorius AG", "life_science_tools"),
    TickerSeed("QIA.DE", "QIAGEN N.V.", "life_science_tools"),
    TickerSeed("ARGX.BR", "argenx SE", "biotech"),
    TickerSeed("UCB.BR", "UCB SA", "pharma"),
    TickerSeed("GALP.AS", "Galapagos NV", "biotech"),
    TickerSeed("RECI.MI", "Recordati S.p.A.", "pharma"),
    TickerSeed("GRF.MC", "Grifols SA", "biotech"),
    TickerSeed("ORNBV.HE", "Orion Oyj", "pharma"),
    TickerSeed("SOBI.ST", "Swedish Orphan Biovitrum AB", "biotech"),
    # --- Asia-Pacific -------------------------------------------------------
    TickerSeed("4502.T", "Takeda Pharmaceutical Co.", "pharma"),
    TickerSeed("4503.T", "Astellas Pharma Inc.", "pharma"),
    TickerSeed("4568.T", "Daiichi Sankyo Co.", "pharma"),
    TickerSeed("4523.T", "Eisai Co.", "pharma"),
    TickerSeed("4519.T", "Chugai Pharmaceutical Co.", "pharma"),
    TickerSeed("4901.T", "FUJIFILM Holdings", "cdmo"),
    TickerSeed("1093.HK", "CSPC Pharmaceutical Group", "pharma"),
    TickerSeed("1177.HK", "Sino Biopharmaceutical", "pharma"),
    TickerSeed("2269.HK", "WuXi Biologics", "cdmo"),
    TickerSeed("6160.HK", "BeiGene Ltd.", "biotech"),
    TickerSeed("603259.SS", "WuXi AppTec Co.", "cro"),
    TickerSeed("600276.SS", "Jiangsu Hengrui Pharmaceuticals", "pharma"),
    TickerSeed("207940.KS", "Samsung Biologics", "cdmo"),
    TickerSeed("068270.KS", "Celltrion Inc.", "biotech"),
    TickerSeed("SUNPHARMA.NS", "Sun Pharmaceutical Industries", "pharma"),
    TickerSeed("DRREDDY.NS", "Dr. Reddy's Laboratories", "pharma"),
    TickerSeed("CIPLA.NS", "Cipla Ltd.", "pharma"),
    TickerSeed("CSL.AX", "CSL Limited", "biotech"),
    # --- North America beyond the US ---------------------------------------
    TickerSeed("BHC.TO", "Bausch Health Companies", "pharma"),
    TickerSeed("CXR.TO", "Cardiol Therapeutics", "biotech"),
)


def load_ticker_seeds() -> list[TickerSeed]:
    """Return the ticker universe, preferring ``data/tickers.csv`` if it exists."""
    if not DATA_FILE.exists():
        return list(SEED_TICKERS)

    seeds: list[TickerSeed] = []
    with DATA_FILE.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ticker = (row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            seeds.append(
                TickerSeed(
                    ticker=ticker,
                    company_name=(row.get("company_name") or ticker).strip(),
                    sector=(row.get("sector") or "unknown").strip(),
                    exchange=(row.get("exchange") or "").strip() or None,
                )
            )
    logger.info("Loaded %d tickers from %s", len(seeds), DATA_FILE)
    return seeds or list(SEED_TICKERS)


async def seed_stocks(db: AsyncSession) -> int:
    """Insert any missing stocks. Returns how many rows were added."""
    seeds = load_ticker_seeds()
    existing = set(
        (await db.execute(select(Stock.ticker))).scalars()
    )

    added = 0
    for seed in seeds:
        if seed.ticker in existing:
            continue
        market = markets.resolve(seed.ticker)
        db.add(
            Stock(
                ticker=seed.ticker,
                company_name=seed.company_name,
                sector=seed.sector,
                # An explicit exchange in the CSV wins; otherwise the symbol
                # suffix names the venue.
                exchange=seed.exchange or market.name,
                mic=market.mic,
                region=market.region,
                country=market.country,
                currency=market.currency,
            )
        )
        added += 1

    if added:
        await db.commit()
        logger.info("Seeded %d new stocks", added)
    return added
