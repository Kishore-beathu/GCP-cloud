"""add duplicate_of to news_articles

Revision ID: 97b42b87dc2c
Revises: 3e9022270db3
Create Date: 2026-08-10 10:44:48.082154

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '97b42b87dc2c'
down_revision: Union[str, Sequence[str], None] = '3e9022270db3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Link a syndicated copy to the earliest copy of the same story.

    batch_alter_table because SQLite cannot ADD a foreign key in place, and the
    development database is SQLite; on PostgreSQL this compiles to a plain
    ALTER TABLE.
    """
    with op.batch_alter_table("news_articles") as batch:
        batch.add_column(sa.Column("duplicate_of_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_news_articles_duplicate_of",
            "news_articles",
            ["duplicate_of_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_news_articles_duplicate_of_id", "news_articles", ["duplicate_of_id"]
    )
    # Existing rows stay NULL, which is correct: everything already stored was
    # ingested as a primary, and back-dating the merge would rewrite history
    # the alerts and backtests were computed against.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_news_articles_duplicate_of_id", table_name="news_articles")
    with op.batch_alter_table("news_articles") as batch:
        batch.drop_constraint("fk_news_articles_duplicate_of", type_="foreignkey")
        batch.drop_column("duplicate_of_id")
