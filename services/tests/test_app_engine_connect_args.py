"""Unit guard — feature 021 / code-review #1: the app (`ac_app`) engine runs through PgBouncer in TRANSACTION mode,
so its asyncpg connect args MUST disable the prepared-statement cache and use process-unique statement names, or a
reused pooled backend raises intermittent `prepared statement "..." already exists / does not exist` under load.

Pure unit: asserts the connect-args recipe directly and never opens a connection (the live through-PgBouncer proof
is `test_pgbouncer_txn_isolation.py`, which needs the compose stack). This guards against a silent regression where
the args are dropped and the bug only reappears under concurrent pooled traffic.
"""
from __future__ import annotations

from api.db import _app_connect_args


def test_app_connect_args_disable_asyncpg_statement_cache():
    assert _app_connect_args()["statement_cache_size"] == 0


def test_app_connect_args_use_unique_statement_names():
    name_func = _app_connect_args()["prepared_statement_name_func"]
    first, second = name_func(), name_func()
    assert first != second                       # unique per call → no cross-client clash on a reused pooled backend
    assert first.startswith("__asyncpg_")
