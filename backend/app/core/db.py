"""Postgres access layer (v4a): lazy connection pool shared by all DB users.

WHY lazy: importing app modules must NEVER open a database connection -
CI and unit tests import these modules with dummy env vars and no real
database. The pool is constructed on first actual use.
"""
import os
from typing import Optional
from psycopg_pool import ConnectionPool
from dotenv import load_dotenv

load_dotenv()

_pool: Optional[ConnectionPool] = None


def get_pool() -> ConnectionPool:
    """Returns the shared connection pool, creating it on first use."""
    global _pool
    if _pool is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError(
                "DATABASE_URL not found. Add it to backend/.env "
                "(Supabase Session pooler URI, port 5432)."
            )
        # WHY: Supabase requires TLS; some connection strings omit the
        # sslmode param, so we enforce it defensively.
        if "sslmode" not in url:
            url += "?sslmode=require"

        # WHY min/max 1-5: free-tier Supabase caps connections hard
        # (Supavisor pools them anyway). Small local pool = small footprint.
        _pool = ConnectionPool(conninfo=url, min_size=1, max_size=5, open=True)
    return _pool