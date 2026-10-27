"""Database connection URLs (09 §7: one AsyncSession per request/job; sync driver only for Alembic)."""
from __future__ import annotations

import os

# asyncpg for the app/runtime/tests (async everywhere on the request path — 00L §4).
ASYNC_URL = os.environ.get(
    "AC_DATABASE_URL", "postgresql+asyncpg://ac:ac_dev_pw@localhost:5433/agent_catalyst"
)
# psycopg (sync) only for out-of-band Alembic migrations (00L §4.1 threadpool/offline escape hatch).
SYNC_URL = os.environ.get(
    "AC_DATABASE_SYNC_URL", "postgresql+psycopg://ac:ac_dev_pw@localhost:5433/agent_catalyst"
)
