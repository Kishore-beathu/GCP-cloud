"""Add valuation_snapshots.

Valuation ratios are stored per capture date rather than as columns on
``stocks``, because a backtest has to know what a ratio *was*. Overwriting a
P/E in place would hand every historical ranking today's figure, which is the
same lookahead the earnings-surprise ranking is built to avoid.

Revision ID: c7f3a1d20e84
Revises: b1c4e77a90f2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7f3a1d20e84"
down_revision: Union[str, Sequence[str], None] = "b1c4e77a90f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "valuation_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("captured_on", sa.Date(), nullable=False),
        sa.Column("pe_ratio", sa.Float(), nullable=True),
        sa.Column("ps_ratio", sa.Float(), nullable=True),
        sa.Column("pb_ratio", sa.Float(), nullable=True),
        sa.Column("ev_ebitda", sa.Float(), nullable=True),
        sa.Column("gross_margin", sa.Float(), nullable=True),
        sa.Column("revenue_growth_yoy", sa.Float(), nullable=True),
        sa.Column("return_on_equity", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticker_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker_id", "captured_on", name="uq_valuation_ticker_captured"
        ),
    )
    op.create_index(
        "ix_valuation_snapshots_ticker_id", "valuation_snapshots", ["ticker_id"]
    )
    op.create_index(
        "ix_valuation_snapshots_captured_on", "valuation_snapshots", ["captured_on"]
    )


def downgrade() -> None:
    op.drop_index("ix_valuation_snapshots_captured_on", table_name="valuation_snapshots")
    op.drop_index("ix_valuation_snapshots_ticker_id", table_name="valuation_snapshots")
    op.drop_table("valuation_snapshots")
