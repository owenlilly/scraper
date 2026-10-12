"""Deleting everything the service holds about one user."""

from psycopg_pool import ConnectionPool

from .db import USER_TABLES

CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def delete_user_data(pool: ConnectionPool, user_id: str) -> None:
    """Remove the user's session, pages, results, history, runs, preferences, usage and agent checkpoints.

    Checkpoint thread ids start with `<user id>:`, which PgSessionStore.session_id guarantees."""
    with pool.connection() as conn:
        for table in USER_TABLES:
            conn.execute(f"DELETE FROM app.{table} WHERE user_id = %s", (user_id,))  # noqa: S608 (fixed table names)
        for table in CHECKPOINT_TABLES:
            if conn.execute("SELECT to_regclass(%s)", (f"app.{table}",)).fetchone()[0] is not None:  # absent until the saver's setup ran
                conn.execute(f"DELETE FROM app.{table} WHERE starts_with(thread_id, %s)", (f"{user_id}:",))  # noqa: S608
