"""Add intraday_bars.

Kept apart from stock_prices, which holds exactly one row per (ticker, trading
day). The backtester, the portfolio valuation and the watchlist's day-over-day
change all read the previous row as the previous close, so minute bars in that
table would quietly redefine "yesterday" for all three.

The table exists so the intraday setups can be measured. Without a record of
what a five-minute chart looked like at 09:47 last Tuesday, their hit rate is
unknowable and the scanner is an opinion.

Revision ID: f81d5b3ac902
Revises: e4a92b7c15d3
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f81d5b3ac902"
down_revision: Union[str, Sequence[str], None] = "e4a92b7c15d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "intraday_bars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False, server_default="5m"),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="yahoo"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticker_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # A recording run re-reads the whole session rather than only the newest
        # bar, so a missed run heals itself. That depends on a repeat store
        # being a no-op.
        sa.UniqueConstraint("ticker_id", "interval", "at", name="uq_intraday_bars_point"),
    )
    op.create_index("ix_intraday_bars_ticker_id", "intraday_bars", ["ticker_id"])
    op.create_index("ix_intraday_bars_interval", "intraday_bars", ["interval"])
    op.create_index("ix_intraday_bars_at", "intraday_bars", ["at"])
    op.create_index(
        "ix_intraday_bars_ticker_at", "intraday_bars", ["ticker_id", "at"]
    )


def downgrade() -> None:
    op.drop_index("ix_intraday_bars_ticker_at", table_name="intraday_bars")
    op.drop_index("ix_intraday_bars_at", table_name="intraday_bars")
    op.drop_index("ix_intraday_bars_interval", table_name="intraday_bars")
    op.drop_index("ix_intraday_bars_ticker_id", table_name="intraday_bars")
    op.drop_table("intraday_bars")
