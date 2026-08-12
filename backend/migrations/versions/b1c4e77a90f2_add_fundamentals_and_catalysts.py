"""Add fundamentals, earnings, analyst trends and forward catalysts

Three gaps this closes, in order of how much evidence stands behind them:

* **Earnings surprise.** The drift after a beat or miss is among the most
  replicated effects in the literature, and nothing here could see it — the
  news pipeline knew a company had *reported*, never whether the number beat.
* **Analyst opinion movement.** A free stand-in for estimate revisions, which
  need a paid feed. The month-on-month *change* is the signal, not the counts.
* **Forward catalysts.** Everything else stored is backward-looking. A
  scheduled, unresolved event is the only kind of information that answers
  "what should I be watching tomorrow".

Plus market cap, which has been a column since the first migration with
nothing ever writing to it.

Revision ID: b1c4e77a90f2
Revises: 97b42b87dc2c
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1c4e77a90f2"
down_revision: Union[str, Sequence[str], None] = "97b42b87dc2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table so SQLite gets a table rebuild rather than an ALTER it
    # does not support; a no-op wrapper on Postgres.
    with op.batch_alter_table("stocks") as batch:
        batch.add_column(sa.Column("shares_outstanding", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column("fundamentals_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.create_table(
        "earnings_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eps_actual", sa.Float(), nullable=True),
        sa.Column("eps_estimate", sa.Float(), nullable=True),
        sa.Column("revenue_actual", sa.Float(), nullable=True),
        sa.Column("revenue_estimate", sa.Float(), nullable=True),
        sa.Column("eps_surprise_pct", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticker_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker_id", "period", name="uq_earnings_ticker_period"),
    )
    op.create_index(
        "ix_earnings_reports_ticker_id", "earnings_reports", ["ticker_id"]
    )
    op.create_index("ix_earnings_reports_period", "earnings_reports", ["period"])
    op.create_index(
        "ix_earnings_reports_reported_at", "earnings_reports", ["reported_at"]
    )

    op.create_table(
        "analyst_trends",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strong_buy", sa.Integer(), nullable=False),
        sa.Column("buy", sa.Integer(), nullable=False),
        sa.Column("hold", sa.Integer(), nullable=False),
        sa.Column("sell", sa.Integer(), nullable=False),
        sa.Column("strong_sell", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticker_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker_id", "period", name="uq_analyst_trend_ticker_period"
        ),
    )
    op.create_index("ix_analyst_trends_ticker_id", "analyst_trends", ["ticker_id"])
    op.create_index("ix_analyst_trends_period", "analyst_trends", ["period"])

    op.create_table(
        "catalyst_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("expected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("url", sa.String(length=1024), nullable=True),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticker_id"], ["stocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker_id", "kind", "expected_at", "external_id",
            name="uq_catalyst_identity",
        ),
    )
    op.create_index("ix_catalyst_events_ticker_id", "catalyst_events", ["ticker_id"])
    op.create_index("ix_catalyst_events_kind", "catalyst_events", ["kind"])
    op.create_index(
        "ix_catalyst_events_expected_at", "catalyst_events", ["expected_at"]
    )
    op.create_index("ix_catalyst_events_source", "catalyst_events", ["source"])


def downgrade() -> None:
    op.drop_table("catalyst_events")
    op.drop_table("analyst_trends")
    op.drop_table("earnings_reports")
    with op.batch_alter_table("stocks") as batch:
        batch.drop_column("fundamentals_at")
        batch.drop_column("shares_outstanding")
