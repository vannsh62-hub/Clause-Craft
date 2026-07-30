"""merge memory and extract heads

Revision ID: b8d4f1e7c206
Revises: 93cfdaed7c8c, a1b2c3d4e5f
Create Date: 2026-07-20 00:00:00.000000

Two revisions were authored independently against `a4edecc1901f` — `93cfdaed7c8c`
(memory facts) and `a1b2c3d4e5f` (extracted documents). Neither is wrong; they simply
branched. `alembic upgrade head` fails on a branched graph with "Multiple head
revisions are present", which is why CI could not run migrations.

This revision has no schema of its own. It exists solely to rejoin the two lineages so
there is a single head again.

Nothing catches this class of defect from the test suite, because `tests/conftest.py`
builds the schema with `Base.metadata.create_all` rather than by running migrations —
so the migration chain is never exercised there. `tests/test_migrations.py` is added
alongside this revision to close that gap.

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "b8d4f1e7c206"
down_revision: Union[str, Sequence[str], None] = ("93cfdaed7c8c", "a1b2c3d4e5f")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A merge revision carries no schema change; both parents are already applied.


def downgrade() -> None:
    """Downgrade schema."""
    # Splitting back into two heads is the inverse, and it is likewise a no-op.
