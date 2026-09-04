"""Add stop, target and setup to trades.

The exit plan used to live inside the free-text rationale, where nothing could
read it. A paper position whose stop was breached while nobody was watching
stayed open, so the log recorded a loss the plan had said to cut — and a record
where losers run and winners are closed at target measures attentiveness rather
than the setup.

Revision ID: a3c7e91f4b58
Revises: f81d5b3ac902
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3c7e91f4b58"
down_revision: Union[str, Sequence[str], None] = "f81d5b3ac902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("trades") as batch:
        batch.add_column(sa.Column("stop", sa.Float(), nullable=True))
        batch.add_column(sa.Column("target", sa.Float(), nullable=True))
        batch.add_column(sa.Column("setup", sa.String(length=64), nullable=True))
    op.create_index("ix_trades_setup", "trades", ["setup"])


def downgrade() -> None:
    op.drop_index("ix_trades_setup", table_name="trades")
    with op.batch_alter_table("trades") as batch:
        batch.drop_column("setup")
        batch.drop_column("target")
        batch.drop_column("stop")
