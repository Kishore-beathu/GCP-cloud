"""Add insider_transactions.

Only open-market purchases and sales are stored. A grant, an option exercise
or a tax withholding is a compensation event and says nothing about what anyone
thinks the stock is worth; keeping them would swamp the deliberate trades with
mechanical ones.

Revision ID: e4a92b7c15d3
Revises: c7f3a1d20e84
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4a92b7c15d3"
down_revision: Union[str, Sequence[str], None] = "c7f3a1d20e84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "insider_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("accession", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("traded_on", sa.Date(), nullable=False),
        sa.Column("filed_on", sa.Date(), nullable=True),
        sa.Column("insider_name", sa.String(length=255), nullable=True),
        sa.Column("insider_title", sa.String(length=255), nullable=True),
        sa.Column("transaction_code", sa.String(length=2), nullable=False),
        sa.Column("shares", sa.Float(), nullable=True),
        sa.Column("price_per_share", sa.Float(), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticker_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("accession", "sequence", name="uq_insider_accession_sequence"),
    )
    op.create_index("ix_insider_transactions_ticker_id", "insider_transactions", ["ticker_id"])
    op.create_index("ix_insider_transactions_accession", "insider_transactions", ["accession"])
    op.create_index("ix_insider_transactions_traded_on", "insider_transactions", ["traded_on"])
    op.create_index(
        "ix_insider_transactions_transaction_code", "insider_transactions", ["transaction_code"]
    )
    op.create_index("ix_insider_ticker_traded", "insider_transactions", ["ticker_id", "traded_on"])


def downgrade() -> None:
    op.drop_index("ix_insider_ticker_traded", table_name="insider_transactions")
    op.drop_index("ix_insider_transactions_transaction_code", table_name="insider_transactions")
    op.drop_index("ix_insider_transactions_traded_on", table_name="insider_transactions")
    op.drop_index("ix_insider_transactions_accession", table_name="insider_transactions")
    op.drop_index("ix_insider_transactions_ticker_id", table_name="insider_transactions")
    op.drop_table("insider_transactions")
