from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..domain import SourceConfig
from ..models import SourceRow


def _source(row: SourceRow) -> SourceConfig:
    return SourceConfig(row.id, row.name, row.enabled, row.schedule, row.settings)


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure(self, name: str, settings: dict | None = None) -> SourceConfig:
        """The source called `name`, created with `settings` if it does not exist yet. Existing settings are left alone:
        operators tune them in the database, and a code default must not overwrite that."""
        statement = (
            insert(SourceRow)
            .values(name=name, settings=settings or {})
            .on_conflict_do_update(index_elements=[SourceRow.name], set_={"name": name})
            .returning(SourceRow)
        )
        return _source(self.session.scalars(statement).one())

    def get(self, name: str) -> SourceConfig | None:
        row = self.session.scalars(select(SourceRow).where(SourceRow.name == name)).one_or_none()
        return _source(row) if row else None
