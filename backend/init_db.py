"""Creates the v4a schema and verifies read/write end to end.
Run once: uv run python init_db.py
"""
import uuid

from app.core.db import get_pool

# WHY these two tables:
# threads       - chat threads (id, title, timestamps). The sidebar lists these.
# thread_events - the agent's SSE event stream, persisted. One row per event.
#                 'after=N' replay = SELECT WHERE seq > N. This is what makes
#                 threads accessible ANYTIME - even after a server restart.
SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL DEFAULT 'New chat',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS thread_events (
    id BIGSERIAL PRIMARY KEY,
    thread_id UUID NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thread_id, seq)
);
"""


def main() -> None:
    pool = get_pool()

    with pool.connection() as conn:
        # WHY execute then explicit commit: pool.connection() opens a
        # transaction; DDL needs it committed to persist.
        conn.execute(SCHEMA)
        conn.commit()

        # --- probe: prove read/write end-to-end, then clean up ---
        row = conn.execute(
            "INSERT INTO threads (title) VALUES (%s) RETURNING id", ("probe",)
        ).fetchone()
        assert row is not None, "INSERT ... RETURNING returned no row"
        probe_id = row[0]

        conn.execute(
            "INSERT INTO thread_events (thread_id, seq, event) VALUES (%s, %s, %s)",
            (probe_id, 0, '{"node": "probe", "status": "ok"}'),
        )
        conn.commit()

        count_row = conn.execute(
            "SELECT COUNT(*) FROM thread_events WHERE thread_id = %s", (probe_id,)
        ).fetchone()
        assert count_row is not None, "COUNT query returned no row"
        count = count_row[0]
        assert count == 1, f"probe event missing: count={count}"

        # WHY cascade check: deleting the thread must delete its events -
        # that cascade is what keeps thread deletion clean forever.
        conn.execute("DELETE FROM threads WHERE id = %s", (probe_id,))
        conn.commit()
        orphaned_row = conn.execute(
            "SELECT COUNT(*) FROM thread_events WHERE thread_id = %s", (probe_id,)
        ).fetchone()
        assert orphaned_row is not None, "COUNT query returned no row"
        orphaned = orphaned_row[0]
        assert orphaned == 0, "cascade delete failed - events orphaned"

    print("DB OK: schema created, insert/read/cascade-delete verified.")


if __name__ == "__main__":
    main()