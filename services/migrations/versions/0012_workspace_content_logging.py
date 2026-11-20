"""0012 workspace_settings.content_logging — per-workspace opt-in for redacted text in call-log lines (feature 023)

Additive, default false (Q1 = A: metadata by default). Missing settings row ⇒ false. RLS unchanged — the table is
already workspace-scoped (0002). Changed only through the admin-only `PUT /api/v1/workspace/telemetry`, which logs
an audit line on every change.

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspace_settings ADD COLUMN content_logging boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE workspace_settings DROP COLUMN content_logging")
