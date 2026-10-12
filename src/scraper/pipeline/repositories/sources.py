from psycopg import Connection
from psycopg.types.json import Jsonb

from ..domain import SourceConfig

COLUMNS = "id, name, enabled, schedule, settings"


class SourceRepository:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def ensure(self, name: str, settings: dict | None = None) -> SourceConfig:
        """The source called `name`, created with `settings` if it does not exist yet. Existing settings are left alone:
        operators tune them in the database, and a code default must not overwrite that."""
        row = self.conn.execute(
            f"INSERT INTO pipeline.source (name, settings) VALUES (%s, %s) "
            f"ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING {COLUMNS}",
            (name, Jsonb(settings or {})),
        ).fetchone()
        return SourceConfig(*row)

    def get(self, name: str) -> SourceConfig | None:
        row = self.conn.execute(f"SELECT {COLUMNS} FROM pipeline.source WHERE name = %s", (name,)).fetchone()
        return SourceConfig(*row) if row else None
