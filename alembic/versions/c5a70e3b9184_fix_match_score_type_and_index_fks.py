"""fix clause match score type and index the extract foreign keys

Revision ID: c5a70e3b9184
Revises: b8d4f1e7c206
Create Date: 2026-07-20 00:05:00.000000

`extracted_clause_matches.score` was declared three different ways at once:

    migration    sa.Numeric()                      -> Postgres NUMERIC, Python Decimal
    column       mapped_column(Integer, default=0) -> SQLAlchemy coerces to int
    annotation   Mapped[float]                     -> mypy believes float

The scores it holds are cosine similarities and keyword counts, so the value is
genuinely a float. Under the previous declarations SQLAlchemy applied Integer
semantics on write, truncating a 0.87 similarity to 0. Settle all three on Float.

Also index the two foreign keys. Both tables are written per upload and read by
`extracted_document_id` / `contract_id`; without an index those reads are sequential
scans, and the ON DELETE CASCADE on each FK takes a full scan of the child table on
every parent delete.

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5a70e3b9184"
down_revision: Union[str, Sequence[str], None] = "b8d4f1e7c206"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "extracted_clause_matches",
        "score",
        existing_type=sa.Numeric(),
        type_=sa.Float(),
        existing_nullable=False,
        # NUMERIC -> DOUBLE PRECISION is not an implicit cast in Postgres.
        postgresql_using="score::double precision",
    )
    op.create_index(
        "ix_extracted_documents_contract_id",
        "extracted_documents",
        ["contract_id"],
    )
    op.create_index(
        "ix_extracted_clause_matches_extracted_document_id",
        "extracted_clause_matches",
        ["extracted_document_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_extracted_clause_matches_extracted_document_id",
        table_name="extracted_clause_matches",
    )
    op.drop_index("ix_extracted_documents_contract_id", table_name="extracted_documents")
    op.alter_column(
        "extracted_clause_matches",
        "score",
        existing_type=sa.Float(),
        type_=sa.Numeric(),
        existing_nullable=False,
        postgresql_using="score::numeric",
    )
