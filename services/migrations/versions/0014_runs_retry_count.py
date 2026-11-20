"""0014 runs.retry_count — provider retries disclosed on the run record (feature 025, US1, FR-015)

`retry_count integer NOT NULL DEFAULT 0`: transport retries spent by the run (Σ(attempts − 1) over model turns and
embedding calls). Existing rows read as 0 — they predate retries, so 0 is truthful. Cost is still reported-usage-only;
this column is the honest disclosure that failed attempts happened (spec FR-015 as amended).

Revision ID: 0014
Revises: 0013
"""
from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE runs ADD COLUMN retry_count integer NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE runs DROP COLUMN retry_count")
