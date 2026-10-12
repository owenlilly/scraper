"""From a dict a spider yielded to a record ready to store: validate, normalise, key, hash."""

import hashlib
import json

from pydantic import BaseModel

from .domain import RecordItem


def prepare(schema: type[BaseModel], natural_key: tuple[str, ...], raw: dict, fetch_id: int | None = None) -> RecordItem:
    """Raises `pydantic.ValidationError` when `raw` does not fit `schema`. The schema's own validators are where field
    normalisation (prices, whitespace, URLs) belongs, so the hash below is of the normalised data: a page that only changed
    its whitespace is not a new version."""
    data = schema.model_validate(raw).model_dump(mode="json")
    key = "|".join(str(data[name]) for name in natural_key)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return RecordItem(key, data, hashlib.sha256(canonical.encode()).hexdigest(), fetch_id)
