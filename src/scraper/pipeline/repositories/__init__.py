"""One class per aggregate. Each holds the queries for its tables and runs them on the `Session` it is given; none of them
commits. The `UnitOfWork` owns the transaction. Nothing here builds SQL from strings: every value is a bound parameter."""

from .fetches import FetchRepository
from .records import RecordRepository
from .runs import RunRepository
from .sources import SourceRepository

__all__ = ["FetchRepository", "RecordRepository", "RunRepository", "SourceRepository"]
