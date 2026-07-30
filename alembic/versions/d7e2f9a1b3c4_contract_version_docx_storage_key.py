"""contract_versions.docx_storage_key — pointer to the rendered DOCX

Lets the export serve the drafting engine's faithful bytes (the edited source, in template
mode) instead of regenerating the document from markdown and losing the original formatting.

Revision ID: d7e2f9a1b3c4
Revises: c5a70e3b9184
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7e2f9a1b3c4"
down_revision: Union[str, Sequence[str], None] = "c5a70e3b9184"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contract_versions",
        sa.Column("docx_storage_key", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contract_versions", "docx_storage_key")
