from sqlalchemy import select, update
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

    def all(self) -> list[SourceConfig]:
        return [_source(row) for row in self.session.scalars(select(SourceRow).order_by(SourceRow.name))]

    def by_id(self, source_id: int) -> SourceConfig | None:
        row = self.session.get(SourceRow, source_id)
        return _source(row) if row else None

    def set_settings(self, source_id: int, settings: dict) -> SourceConfig:
        """Replace the saved settings. The only writer: the API validates before calling it."""
        row = self.session.scalars(
            update(SourceRow).where(SourceRow.id == source_id).values(settings=settings).returning(SourceRow).execution_options(populate_existing=True)
        ).one()
        return _source(row)
