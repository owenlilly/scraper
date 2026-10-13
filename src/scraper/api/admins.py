"""Is this person an administrator? The web app owns that fact, in `auth."user"`; the API may read only these three columns."""

import psycopg
from psycopg_pool import ConnectionPool


class AdminCheckUnavailable(Exception):
    """The table cannot be read: the web app's schema is missing, or this database role was not granted the three columns."""


def is_admin(pool: ConnectionPool, user_id: str) -> bool:
    """Checked on every request, so a banned or demoted administrator loses access at once, not when their token expires."""
    try:
        with pool.connection() as conn:
            row = conn.execute('SELECT role, banned FROM auth."user" WHERE id = %s', (user_id,)).fetchone()
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege, psycopg.errors.InvalidSchemaName) as error:
        raise AdminCheckUnavailable from error
    return row is not None and row[0] == "admin" and not row[1]
