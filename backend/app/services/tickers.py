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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Stock
from app.services import markets, sectors

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
    # Charles River is the largest preclinical CRO; "cdmo" described what it
    # sells least. Both sectors sit in the same group, so this changes how it
    # is labelled and ranked within the CRO cohort, not which group it is in.
    TickerSeed("CRL", "Charles River Laboratories", "cro", "NYSE"),
    TickerSeed("IQV", "IQVIA Holdings Inc.", "cro", "NYSE"),
    TickerSeed("ICLR", "ICON plc", "cro", "NASDAQ"),
    TickerSeed("MEDP", "Medpace Holdings", "cro", "NASDAQ"),
    TickerSeed("FTRE", "Fortrea Holdings Inc.", "cro", "NASDAQ"),
    TickerSeed("CERT", "Certara Inc.", "cro", "NASDAQ"),
    # Inotiv (NOTV) was seeded here and removed: it filed Chapter 11 in June
    # 2026, moved to NOTVQ, and trades around a $0.6M market cap. Left as a
    # note rather than deleted silently, because a preclinical CRO vanishing
    # from the cohort otherwise looks like an oversight.
    # --- Artificial intelligence ---------------------------------------------
    # Platform and model owners.
    # Semiconductors that are not an AI trade. Analog, mixed-signal, RF, power
    # and the mature-node foundries: they sell into cars, factories, handsets
    # and appliances, and their cycle is inventory and end demand rather than
    # cluster build-out. Kept out of ai_semiconductor deliberately — that
    # sector is defined as names whose valuation moves on AI demand, and
    # filing Texas Instruments under it would have made the label untrue and
    # diluted the cohort these are ranked against.
    TickerSeed("TXN", "Texas Instruments Incorporated", "semiconductor", "NASDAQ"),
    TickerSeed("ADI", "Analog Devices Inc.", "semiconductor", "NASDAQ"),
    TickerSeed("NXPI", "NXP Semiconductors N.V.", "semiconductor", "NASDAQ"),
    TickerSeed("MCHP", "Microchip Technology Incorporated", "semiconductor", "NASDAQ"),
    TickerSeed("ON", "ON Semiconductor Corporation", "semiconductor", "NASDAQ"),
    TickerSeed("SWKS", "Skyworks Solutions Inc.", "semiconductor", "NASDAQ"),
    TickerSeed("QRVO", "Qorvo Inc.", "semiconductor", "NASDAQ"),
    TickerSeed("WOLF", "Wolfspeed Inc.", "semiconductor", "NYSE"),
    TickerSeed("LSCC", "Lattice Semiconductor Corporation", "semiconductor", "NASDAQ"),
    TickerSeed("CRUS", "Cirrus Logic Inc.", "semiconductor", "NASDAQ"),
    TickerSeed("MTSI", "MACOM Technology Solutions Holdings", "semiconductor", "NASDAQ"),
    TickerSeed("GFS", "GlobalFoundries Inc.", "semiconductor", "NASDAQ"),
    TickerSeed("TSEM", "Tower Semiconductor Ltd.", "semiconductor", "NASDAQ"),
    TickerSeed("2303.TW", "United Microelectronics Corporation", "semiconductor"),
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
    # Pure Storage rebranded to Everpure and moved PSTG -> P in April 2026.
    TickerSeed("P", "Everpure Inc.", "storage_hardware", "NYSE"),
    TickerSeed("DELL", "Dell Technologies Inc.", "storage_hardware", "NYSE"),
    TickerSeed("HPE", "Hewlett Packard Enterprise", "storage_hardware", "NYSE"),
    # Hitachi owns Vantara, one of the few remaining enterprise storage
    # businesses not already represented here, and sells the grid equipment
    # the same build-out needs. It sits in storage rather than power because
    # storage is the part that competes with NetApp and Dell directly.
    TickerSeed("6501.T", "Hitachi Ltd.", "storage_hardware", "TSE"),
    TickerSeed("MU", "Micron Technology Inc.", "memory", "NASDAQ"),
    TickerSeed("000660.KS", "SK hynix Inc.", "memory", "KRX"),
    TickerSeed("2408.TW", "Nanya Technology Corporation", "memory", "TWSE"),
    # The two pure-play NAND names, which the cohort had neither of. SanDisk
    # was spun out of Western Digital in February 2025 and is the flash half of
    # what WDC used to be; Kioxia listed in Tokyo in December 2024. They run
    # the Yokkaichi and Kitakami fabs as a joint venture, so they move together
    # and neither is a substitute for the other.
    TickerSeed("SNDK", "SanDisk Corporation", "memory", "NASDAQ"),
    TickerSeed("285A.T", "Kioxia Holdings Corporation", "memory"),
    TickerSeed("NTNX", "Nutanix Inc.", "storage_hardware", "NASDAQ"),
    # Data platforms: where the data is queried rather than kept.
    TickerSeed("SNOW", "Snowflake Inc.", "data_platform", "NYSE"),
    TickerSeed("MDB", "MongoDB Inc.", "data_platform", "NASDAQ"),
    # Confluent (CFLT) was removed: IBM completed its acquisition in March
    # 2026 and the shares were delisted from Nasdaq.
    TickerSeed("ORCL", "Oracle Corporation", "data_platform", "NYSE"),
    TickerSeed("TDC", "Teradata Corporation", "data_platform", "NYSE"),
    # The buildings. REITs, so they trade on leasing and power, not on chips.
    TickerSeed("EQIX", "Equinix Inc.", "data_center", "NASDAQ"),
    TickerSeed("DLR", "Digital Realty Trust", "data_center", "NYSE"),
    # --- AI, deepened --------------------------------------------------------
    # More silicon. Intel and Qualcomm are the incumbents AI is reshaping;
    # the rest sell into the accelerator build-out directly.
    TickerSeed("INTC", "Intel Corporation", "ai_semiconductor", "NASDAQ"),
    TickerSeed("QCOM", "QUALCOMM Incorporated", "ai_semiconductor", "NASDAQ"),
    TickerSeed("SITM", "SiTime Corporation", "ai_semiconductor", "NASDAQ"),
    TickerSeed("ALAB", "Astera Labs Inc.", "ai_semiconductor", "NASDAQ"),
    TickerSeed("CRDO", "Credo Technology Group", "ai_semiconductor", "NASDAQ"),
    TickerSeed("MPWR", "Monolithic Power Systems", "ai_semiconductor", "NASDAQ"),
    TickerSeed("STM", "STMicroelectronics NV (ADR)", "ai_semiconductor", "NYSE"),
    TickerSeed("IFX.DE", "Infineon Technologies AG", "ai_semiconductor", "XETRA"),
    TickerSeed("2454.TW", "MediaTek Inc.", "ai_semiconductor", "TWSE"),
    TickerSeed("0981.HK", "Semiconductor Manufacturing International", "ai_semiconductor", "HKEX"),
    # Fabrication equipment and design tools. These trade on capital spending
    # plans rather than on chip volumes, so they lead the cycle.
    TickerSeed("AMAT", "Applied Materials Inc.", "ai_equipment", "NASDAQ"),
    TickerSeed("LRCX", "Lam Research Corporation", "ai_equipment", "NASDAQ"),
    TickerSeed("KLAC", "KLA Corporation", "ai_equipment", "NASDAQ"),
    # Test, materials, metrology and inspection. The cohort held the four big
    # deposition and lithography names and almost nothing else, which meant it
    # tracked the fabs' capital budget and missed the consumables and test
    # spend that continues between capex cycles.
    TickerSeed("TER", "Teradyne Inc.", "ai_equipment", "NASDAQ"),
    TickerSeed("ENTG", "Entegris Inc.", "ai_equipment", "NASDAQ"),
    TickerSeed("MKSI", "MKS Instruments Inc.", "ai_equipment", "NASDAQ"),
    TickerSeed("ONTO", "Onto Innovation Inc.", "ai_equipment", "NYSE"),
    TickerSeed("ACLS", "Axcelis Technologies Inc.", "ai_equipment", "NASDAQ"),
    TickerSeed("NVMI", "Nova Ltd.", "ai_equipment", "NASDAQ"),
    TickerSeed("CAMT", "Camtek Ltd.", "ai_equipment", "NASDAQ"),
    TickerSeed("6146.T", "Disco Corporation", "ai_equipment", "TSE"),
    TickerSeed("6920.T", "Lasertec Corporation", "ai_equipment", "TSE"),
    TickerSeed("7735.T", "SCREEN Holdings Co. Ltd.", "ai_equipment", "TSE"),
    TickerSeed("6525.T", "Kokusai Electric Corporation", "ai_equipment", "TSE"),
    TickerSeed("SNPS", "Synopsys Inc.", "ai_equipment", "NASDAQ"),
    TickerSeed("CDNS", "Cadence Design Systems", "ai_equipment", "NASDAQ"),
    TickerSeed("8035.T", "Tokyo Electron Ltd.", "ai_equipment", "TSE"),
    TickerSeed("6857.T", "Advantest Corporation", "ai_equipment", "TSE"),
    TickerSeed("ASM.AS", "ASM International NV", "ai_equipment", "Euronext Amsterdam"),
    TickerSeed("BESI.AS", "BE Semiconductor Industries", "ai_equipment", "Euronext Amsterdam"),
    # Cluster interconnect and optics: a GPU fleet is only as fast as the
    # fabric between the racks, which is why these move on the same orders.
    TickerSeed("ANET", "Arista Networks Inc.", "ai_networking", "NYSE"),
    TickerSeed("COHR", "Coherent Corp.", "ai_networking", "NYSE"),
    TickerSeed("LITE", "Lumentum Holdings Inc.", "ai_networking", "NASDAQ"),
    TickerSeed("CIEN", "Ciena Corporation", "ai_networking", "NYSE"),
    # Optics and switching. Four names could not carry a sector that decides
    # how much of a cluster's cost is interconnect: the transceiver makers and
    # the switch ODM belong here as much as the systems vendors do.
    TickerSeed("CSCO", "Cisco Systems Inc.", "ai_networking", "NASDAQ"),
    TickerSeed("FN", "Fabrinet", "ai_networking", "NYSE"),
    TickerSeed("AAOI", "Applied Optoelectronics Inc.", "ai_networking", "NASDAQ"),
    TickerSeed("SMTC", "Semtech Corporation", "ai_networking", "NASDAQ"),
    TickerSeed("MXL", "MaxLinear Inc.", "ai_networking", "NASDAQ"),
    TickerSeed("2345.TW", "Accton Technology Corporation", "ai_networking"),
    # Platforms and applications.
    TickerSeed("IBM", "International Business Machines", "ai_tech", "NYSE"),
    TickerSeed("SMCI", "Super Micro Computer Inc.", "ai_tech", "NASDAQ"),
    TickerSeed("BIDU", "Baidu Inc. (ADR)", "ai_tech", "NASDAQ"),
    TickerSeed("9988.HK", "Alibaba Group Holding", "ai_tech", "HKEX"),
    TickerSeed("0700.HK", "Tencent Holdings Ltd.", "ai_tech", "HKEX"),
    TickerSeed("ADBE", "Adobe Inc.", "ai_software", "NASDAQ"),
    TickerSeed("SAP", "SAP SE (ADR)", "ai_software", "NYSE"),
    TickerSeed("AI", "C3.ai Inc.", "ai_software", "NYSE"),
    TickerSeed("PATH", "UiPath Inc.", "ai_software", "NYSE"),
    TickerSeed("SOUN", "SoundHound AI Inc.", "ai_software", "NASDAQ"),
    # --- Data storage, deepened ----------------------------------------------
    TickerSeed("005930.KS", "Samsung Electronics Co.", "memory", "KRX"),
    TickerSeed("QMCO", "Quantum Corporation", "storage_hardware", "NASDAQ"),
    # Storage sold as a service rather than as a box.
    TickerSeed("DBX", "Dropbox Inc.", "cloud_storage", "NASDAQ"),
    TickerSeed("BOX", "Box Inc.", "cloud_storage", "NYSE"),
    TickerSeed("AKAM", "Akamai Technologies Inc.", "cloud_storage", "NASDAQ"),
    TickerSeed("NET", "Cloudflare Inc.", "cloud_storage", "NYSE"),
    TickerSeed("RXT", "Rackspace Technology Inc.", "cloud_storage", "NASDAQ"),
    # Query and observability layers over stored data.
    TickerSeed("DDOG", "Datadog Inc.", "data_platform", "NASDAQ"),
    TickerSeed("ESTC", "Elastic NV", "data_platform", "NYSE"),
    # More buildings, including the operators built specifically for AI
    # workloads — these trade on power availability more than on floor space.
    TickerSeed("IRM", "Iron Mountain Inc.", "data_center", "NYSE"),
    TickerSeed("VRT", "Vertiv Holdings Co.", "data_center", "NYSE"),
    TickerSeed("GDS", "GDS Holdings Ltd. (ADR)", "data_center", "NASDAQ"),
    TickerSeed("9698.HK", "GDS Holdings Ltd.", "data_center", "HKEX"),
    TickerSeed("APLD", "Applied Digital Corporation", "data_center", "NASDAQ"),
    TickerSeed("IREN", "IREN Limited", "data_center", "NASDAQ"),
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
    # GALP is Galp Energia, a Portuguese oil company. Galapagos is GLPG.
    TickerSeed("GLPG.AS", "Galapagos NV", "biotech"),
    TickerSeed("REC.MI", "Recordati S.p.A.", "pharma"),
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
    # --- Contract research organisations, non-US listings -------------------
    # The CRO cohort was four names, all but one US-listed, while most of the
    # industry's capacity is in Asia. Chinese CROs are listed twice on purpose:
    # the A-share and the H-share trade on different sessions and in different
    # currencies, and the matcher indexes several symbols per company name so
    # both lines receive the same news rather than only the one that happened
    # to be indexed.
    # CMIC Holdings (2309.T) was removed: delisted from the TSE in March
    # 2024 in a management buyout, so it had been private for two years
    # by the time it was added here.
    TickerSeed("2395.T", "Shin Nippon Biomedical Laboratories", "cro"),
    TickerSeed("300347.SZ", "Hangzhou Tigermed Consulting Co.", "cro"),
    TickerSeed("3347.HK", "Hangzhou Tigermed Consulting Co.", "cro"),
    TickerSeed("300759.SZ", "Pharmaron Beijing Co.", "cro"),
    TickerSeed("3759.HK", "Pharmaron Beijing Co.", "cro"),
    TickerSeed("603127.SS", "Joinn Laboratories (China) Co.", "cro"),
    TickerSeed("6127.HK", "Joinn Laboratories (China) Co.", "cro"),
    TickerSeed("688202.SS", "Shanghai Medicilon Inc.", "cro"),
    TickerSeed("1521.HK", "Frontage Holdings Corp.", "cro"),
    # WuXi AppTec's A-share is already tracked above; this is its Hong Kong
    # line, which is where its international news flow tends to land first.
    TickerSeed("2359.HK", "WuXi AppTec Co.", "cro"),
    TickerSeed("SYNGENE.NS", "Syngene International Ltd.", "cro"),
    # --- Data storage, widened -----------------------------------------------
    # The smallest ranking cohort at 32 names, which made its percentiles move
    # in steps of three points: a rank there carried a fraction of the
    # information the same rank carried in a 145-name group. Cross-sectional
    # ranking is only as good as the cohort it ranks against, so this is the
    # cheapest available improvement to score quality — no new code, no new
    # vendor, just more of what the percentile is computed over.
    #
    # Paced against the vendor rate limit rather than added to exhaustion:
    # roughly 60 calls a minute sets how fast the whole universe refreshes, and
    # a universe that cannot be refreshed inside an ingest cycle is worse than
    # a smaller one that can.
    #
    # Servers: the machines the racks are filled with. Almost all Taiwanese
    # ODMs, who build to hyperscaler order rather than selling a brand, so they
    # move on order flow ahead of the end demand that drives everyone else here.
    TickerSeed("2317.TW", "Hon Hai Precision Industry", "server_hardware"),
    TickerSeed("2382.TW", "Quanta Computer Inc.", "server_hardware"),
    TickerSeed("3231.TW", "Wistron Corporation", "server_hardware"),
    TickerSeed("6669.TW", "Wiwynn Corporation", "server_hardware"),
    # The branded builders, as against the ODMs above. They sell their own
    # nameplate into enterprises rather than building to a hyperscaler's
    # drawing, so they carry inventory and pricing risk the ODMs do not.
    TickerSeed("0992.HK", "Lenovo Group Limited", "server_hardware", "HKEX"),
    TickerSeed("2376.TW", "Gigabyte Technology Co. Ltd.", "server_hardware"),
    TickerSeed("2357.TW", "ASUSTeK Computer Inc.", "server_hardware"),
    # The contract manufacturers. They assemble the racks the ODMs above
    # design and the hyperscalers order, and they are the North American way
    # into a build-out otherwise expressed almost entirely in Taipei.
    TickerSeed("CLS", "Celestica Inc.", "server_hardware", "NYSE"),
    TickerSeed("FLEX", "Flex Ltd.", "server_hardware", "NASDAQ"),
    TickerSeed("JBL", "Jabil Inc.", "server_hardware", "NYSE"),
    TickerSeed("6702.T", "Fujitsu Limited", "server_hardware", "TSE"),
    TickerSeed("6701.T", "NEC Corporation", "server_hardware", "TSE"),
    TickerSeed("2356.TW", "Inventec Corporation", "server_hardware"),
    TickerSeed("000977.SZ", "Inspur Electronic Information", "server_hardware"),
    # Memory, deepened. The cohort had the three big DRAM makers and little of
    # the NAND and controller layer that moves with them.
    TickerSeed("2344.TW", "Winbond Electronics Corp.", "memory"),
    TickerSeed("2337.TW", "Macronix International", "memory"),
    TickerSeed("8299.TWO", "Phison Electronics Corp.", "memory"),
    TickerSeed("SIMO", "Silicon Motion Technology (ADR)", "memory", "NASDAQ"),
    # Module makers. They buy DRAM and NAND wafers and sell finished modules,
    # so their margin moves inversely to the spot price the fabs above earn on
    # it — the cohort had every seller of memory and no buyer of it.
    TickerSeed("3260.TWO", "ADATA Technology Co. Ltd.", "memory"),
    TickerSeed("2451.TW", "Transcend Information Inc.", "memory"),
    TickerSeed("RMBS", "Rambus Inc.", "memory", "NASDAQ"),
    TickerSeed("NLST", "Netlist Inc.", "memory", "NASDAQ"),
    TickerSeed("MRAM", "Everspin Technologies", "memory", "NASDAQ"),
    # Data platforms and the software that protects what is on them.
    TickerSeed("DT", "Dynatrace Inc.", "data_platform", "NYSE"),
    TickerSeed("PRGS", "Progress Software Corporation", "data_platform", "NASDAQ"),
    TickerSeed("CVLT", "Commvault Systems Inc.", "data_platform", "NASDAQ"),
    TickerSeed("RBRK", "Rubrik Inc.", "data_platform", "NYSE"),
    TickerSeed("VRNS", "Varonis Systems Inc.", "data_platform", "NASDAQ"),
    TickerSeed("AMPL", "Amplitude Inc.", "data_platform", "NASDAQ"),
    TickerSeed("OTEX", "Open Text Corporation", "data_platform", "NASDAQ"),
    TickerSeed("DOMO", "Domo Inc.", "data_platform", "NASDAQ"),
    # Storage sold as a service.
    TickerSeed("BLZE", "Backblaze Inc.", "cloud_storage", "NASDAQ"),
    TickerSeed("FSLY", "Fastly Inc.", "cloud_storage", "NYSE"),
    TickerSeed("DOCN", "DigitalOcean Holdings", "cloud_storage", "NYSE"),
    # More buildings, including the operators that pivoted from mining to AI
    # compute — they own the two things that are actually scarce, interconnect
    # and a grid connection.
    TickerSeed("CRWV", "CoreWeave Inc.", "data_center", "NASDAQ"),
    TickerSeed("NBIS", "Nebius Group N.V.", "data_center", "NASDAQ"),
    TickerSeed("CIFR", "Cipher Mining Inc.", "data_center", "NASDAQ"),
    TickerSeed("WULF", "TeraWulf Inc.", "data_center", "NASDAQ"),
    # Miners that have turned their power contracts and shells into AI hosting.
    # They belong with the data centres rather than anywhere crypto-shaped:
    # what they sell now is compute capacity, and they re-rate on hosting deals
    # rather than on the coin price.
    TickerSeed("RIOT", "Riot Platforms Inc.", "data_center", "NASDAQ"),
    TickerSeed("MARA", "MARA Holdings Inc.", "data_center", "NASDAQ"),
    TickerSeed("CORZ", "Core Scientific Inc.", "data_center", "NASDAQ"),
    # Landlords and developers, as against operators.
    TickerSeed("DBRG", "DigitalBridge Group Inc.", "data_center", "NYSE"),
    TickerSeed("AJBU.SI", "Keppel DC REIT", "data_center", "SGX"),
    TickerSeed("GMG.AX", "Goodman Group", "data_center", "ASX"),
    TickerSeed("HUT", "Hut 8 Corp.", "data_center", "NASDAQ"),
    TickerSeed("GLXY", "Galaxy Digital Inc.", "data_center", "NASDAQ"),
    TickerSeed("VNET", "VNET Group Inc. (ADR)", "data_center", "NASDAQ"),
    TickerSeed("NXT.AX", "NEXTDC Limited", "data_center"),
    # Power and cooling. Not a data-centre cohort in disguise: a REIT leasing
    # floor space and a company selling switchgear answer the same demand
    # through different mechanics, and it is power availability rather than
    # floor space that decides how many of these get built at all.
    TickerSeed("ETN", "Eaton Corporation plc", "datacenter_power", "NYSE"),
    TickerSeed("NVT", "nVent Electric plc", "datacenter_power", "NYSE"),
    TickerSeed("MOD", "Modine Manufacturing Company", "datacenter_power", "NYSE"),
    TickerSeed("GNRC", "Generac Holdings Inc.", "datacenter_power", "NYSE"),
    TickerSeed("BE", "Bloom Energy Corporation", "datacenter_power", "NYSE"),
    TickerSeed("GEV", "GE Vernova Inc.", "datacenter_power", "NYSE"),
    TickerSeed("PWR", "Quanta Services Inc.", "datacenter_power", "NYSE"),
    TickerSeed("FIX", "Comfort Systems USA", "datacenter_power", "NYSE"),
    TickerSeed("EME", "EMCOR Group Inc.", "datacenter_power", "NYSE"),
    TickerSeed("VST", "Vistra Corp.", "datacenter_power", "NYSE"),
    # Switchgear and electrical distribution — the equipment between the grid
    # and the rack, and the part of the build-out with the longest lead times.
    TickerSeed("POWL", "Powell Industries Inc.", "datacenter_power", "NASDAQ"),
    TickerSeed("HUBB", "Hubbell Incorporated", "datacenter_power", "NYSE"),
    TickerSeed("ATKR", "Atkore Inc.", "datacenter_power", "NYSE"),
    TickerSeed("ENR.DE", "Siemens Energy AG", "datacenter_power", "XETRA"),
    TickerSeed("7011.T", "Mitsubishi Heavy Industries", "datacenter_power", "TSE"),
    # Small modular reactors. Speculative and pre-revenue, and included for
    # that reason rather than despite it: they trade almost entirely on
    # data-centre power announcements, which is the signal this cohort exists
    # to catch. Neither has a reactor operating.
    TickerSeed("OKLO", "Oklo Inc.", "datacenter_power", "NYSE"),
    TickerSeed("SMR", "NuScale Power Corporation", "datacenter_power", "NYSE"),
    TickerSeed("CEG", "Constellation Energy Corporation", "datacenter_power", "NASDAQ"),
    TickerSeed("TLN", "Talen Energy Corporation", "datacenter_power", "NASDAQ"),
    TickerSeed("NRG", "NRG Energy Inc.", "datacenter_power", "NYSE"),
    TickerSeed("SU.PA", "Schneider Electric SE", "datacenter_power"),
    TickerSeed("LR.PA", "Legrand SA", "datacenter_power"),
    TickerSeed("ABBN.SW", "ABB Ltd", "datacenter_power"),
    # --- North America beyond the US ---------------------------------------
    TickerSeed("BHC.TO", "Bausch Health Companies", "pharma"),
    # CXR.TO was Concordia International, long delisted. Cardiol is CRDL.
    TickerSeed("CRDL.TO", "Cardiol Therapeutics", "clinical_stage"),
    # Both Canadians above are dual-listed, and only the US line is reachable
    # from a Revolut account — it carries US-listed stocks only. The Toronto
    # lines stay because they carry the domestic session and the home-market
    # news flow; these are the ones actually tradeable.
    TickerSeed("BHC", "Bausch Health Companies", "pharma", "NYSE"),
    TickerSeed("CRDL", "Cardiol Therapeutics", "clinical_stage", "NASDAQ"),
    # --- Clinical-stage: North America --------------------------------------
    # The cohort this platform was missing. Everything above with a "biotech"
    # label sells something; each name below is pre-revenue and re-rates on a
    # single scheduled event, which is the behaviour the catalyst calendar and
    # the setup scanner were built around.
    #
    # Four rules decided the list, and each one excludes names worth holding:
    #
    # 1. **A US primary listing.** Revolut carries US-listed stocks only —
    #    roughly 4,000 across NYSE, Nasdaq and NYSE American — so a TSX or
    #    ASX line is not reachable from that account however good the company.
    #    Canadian issuers appear here under their Nasdaq symbol (XENE, ABCL,
    #    ZYME, CRDL), which is why the cohort is North American rather than US.
    # 2. **No approved product.** The boundary moves in one direction and
    #    quietly: Nuvation and Crinetics were both clinical-stage until an
    #    approval landed, and they are deliberately absent for that reason.
    # 3. **Comfortably clear of $50M.** Not near it. A name sitting at the
    #    threshold crosses it on an ordinary day's move, and a universe whose
    #    membership changes with the tape is not a universe.
    # 4. **Not under a definitive acquisition agreement.** A company being
    #    bought stops trading on its science and starts trading on the spread
    #    to the offer, so it is noise in a catalyst scanner. That rule removed
    #    four names during this pass alone — Arcellx (Gilead, closed April
    #    2026), Terns (Merck, closed May 2026), Merus (Genmab, closed December
    #    2025) and Apogee (AbbVie, agreed June 2026) — which is the rate this
    #    cohort turns over at, and the reason the list below is a starting
    #    point to verify rather than a fact to trust.
    #
    # Verify the whole cohort in one call once the vendor key works:
    #   GET /fundamentals?group=pharma_life_sciences&min_market_cap=50000000
    # Anything that reports a null market cap there is either uncovered or no
    # longer trading, and both are worth knowing before sizing a position.
    #
    # Metabolic and cardiovascular.
    TickerSeed("VKTX", "Viking Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("AKRO", "Akero Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("ALT", "Altimmune Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("MLYS", "Mineralys Therapeutics", "clinical_stage", "NASDAQ"),
    # Oncology, the deepest part of the cohort and the most catalyst-driven.
    TickerSeed("SMMT", "Summit Therapeutics Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("NUVL", "Nuvalent Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("RVMD", "Revolution Medicines", "clinical_stage", "NASDAQ"),
    TickerSeed("IDYA", "IDEAYA Biosciences", "clinical_stage", "NASDAQ"),
    TickerSeed("KURA", "Kura Oncology Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("ARVN", "Arvinas Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("NRIX", "Nurix Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("RLAY", "Relay Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("ERAS", "Erasca Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("OLMA", "Olema Pharmaceuticals", "clinical_stage", "NASDAQ"),
    TickerSeed("CCCC", "C4 Therapeutics Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("ORIC", "ORIC Pharmaceuticals", "clinical_stage", "NASDAQ"),
    TickerSeed("TNGX", "Tango Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("COGT", "Cogent Biosciences", "clinical_stage", "NASDAQ"),
    TickerSeed("CGEM", "Cullinan Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("REPL", "Replimune Group Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("ELVN", "Enliven Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("CLDX", "Celldex Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("JANX", "Janux Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("KYMR", "Kymera Therapeutics", "clinical_stage", "NASDAQ"),
    # Genetic medicine: gene editing, gene therapy and cell therapy. The
    # highest-variance names here — a single readout has repeatedly moved
    # these 50% in a session, in both directions.
    TickerSeed("NTLA", "Intellia Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("BEAM", "Beam Therapeutics Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("PRME", "Prime Medicine Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("EDIT", "Editas Medicine Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("SANA", "Sana Biotechnology", "clinical_stage", "NASDAQ"),
    TickerSeed("RCKT", "Rocket Pharmaceuticals", "clinical_stage", "NASDAQ"),
    TickerSeed("ALLO", "Allogene Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("FATE", "Fate Therapeutics Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("CRBU", "Caribou Biosciences", "clinical_stage", "NASDAQ"),
    TickerSeed("ARCT", "Arcturus Therapeutics", "clinical_stage", "NASDAQ"),
    # Immunology and inflammation.
    TickerSeed("IMVT", "Immunovant Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("VERA", "Vera Therapeutics Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("SYRE", "Spyre Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("VRDN", "Viridian Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("KROS", "Keros Therapeutics Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("PTGX", "Protagonist Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("SRRK", "Scholar Rock Holding", "clinical_stage", "NASDAQ"),
    TickerSeed("SVRA", "Savara Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("ANNX", "Annexon Inc.", "clinical_stage", "NASDAQ"),
    # Neurology and psychiatry.
    TickerSeed("DNLI", "Denali Therapeutics", "clinical_stage", "NASDAQ"),
    TickerSeed("PRAX", "Praxis Precision Medicines", "clinical_stage", "NASDAQ"),
    TickerSeed("MNMD", "Mind Medicine (MindMed)", "clinical_stage", "NASDAQ"),
    # Canadian issuers on their US line, tradeable where the TSX line is not.
    TickerSeed("XENE", "Xenon Pharmaceuticals", "clinical_stage", "NASDAQ"),
    TickerSeed("ABCL", "AbCellera Biologics Inc.", "clinical_stage", "NASDAQ"),
    TickerSeed("ZYME", "Zymeworks Inc.", "clinical_stage", "NASDAQ"),
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


async def seed_stocks(db: AsyncSession) -> dict[str, int]:
    """Insert missing stocks and reconcile the sector of existing ones.

    Inserting only was enough while the universe grew by addition, but a
    reclassification then reached a fresh clone and never reached a database
    that already had the row — so the same code produced two different sector
    maps depending on when the database was first seeded, and the group a
    symbol was ranked in depended on that history rather than on the code.

    Only the sector is reconciled. Company name, exchange and currency are
    left alone: those are corrected from live vendor data elsewhere, and
    overwriting them here would undo that on every startup.

    Symbols that leave the seed list are deactivated rather than deleted.
    Tickers get renamed and companies get acquired — Pure Storage became
    Everpure and moved PSTG to P, Confluent was bought and delisted — and
    without this the old symbol stayed in the watchlist forever, priceless
    and unexplained, while the replacement was added beside it. Deactivating
    keeps the stored history and reverses itself if the symbol returns;
    deleting would throw away price rows that are still true.
    """
    seeds = load_ticker_seeds()
    seeded = {seed.ticker for seed in seeds}
    existing = {
        ticker: sector
        for ticker, sector in (await db.execute(select(Stock.ticker, Stock.sector))).all()
    }

    added = 0
    reclassified = 0
    for seed in seeds:
        if seed.ticker in existing:
            if existing[seed.ticker] != seed.sector:
                stock = (
                    await db.execute(select(Stock).where(Stock.ticker == seed.ticker))
                ).scalar_one()
                logger.info(
                    "Reclassifying %s from %s to %s",
                    seed.ticker,
                    stock.sector,
                    seed.sector,
                )
                stock.sector = seed.sector
                reclassified += 1
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

    retired = 0
    restored = 0
    for stock in (await db.execute(select(Stock))).scalars():
        wanted = stock.ticker in seeded
        if stock.is_active and not wanted:
            logger.info("Retiring %s: no longer in the seed list", stock.ticker)
            stock.is_active = False
            retired += 1
        elif not stock.is_active and wanted:
            logger.info("Restoring %s: back in the seed list", stock.ticker)
            stock.is_active = True
            restored += 1

    if added or reclassified or retired or restored:
        await db.commit()
        logger.info(
            "Seeded %d new stocks, reclassified %d, retired %d, restored %d",
            added,
            reclassified,
            retired,
            restored,
        )
    return {
        "added": added,
        "reclassified": reclassified,
        "retired": retired,
        "restored": restored,
    }


async def tickers_in_group(db: AsyncSession, group_key: str) -> list[str]:
    """Active symbols belonging to an industry group.

    Lets an ingest be aimed at "the data storage stocks" rather than at a
    hand-typed list of symbols that goes stale the moment the universe grows.
    Raises LookupError for an unknown group so a typo cannot quietly widen the
    run to the whole universe.
    """
    members = sectors.sectors_in(group_key.strip().lower())
    if not members:
        raise LookupError(
            f"Unknown group {group_key!r}. Known groups: "
            + ", ".join(group.key for group in sectors.all_groups())
        )

    rows = await db.execute(
        select(Stock.ticker)
        .where(Stock.is_active.is_(True), func.lower(Stock.sector).in_(members))
        .order_by(Stock.ticker)
    )
    return list(rows.scalars())
