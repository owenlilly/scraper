"""What an administrator can change about a spider.

Every spider has a settings model (this module's `SpiderSettings`, or a subclass that adds the spider's own). The model is the one
place that says what is allowed: the API validates every save against it, a run is given a validated snapshot of it, and the page's
form is drawn from a description of it. Nothing here imports spider code: a "spider class" is anything with the attributes read below."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

TRIAL_MAX = 20
SCALARS = {"integer", "number", "string", "boolean"}


class SpiderSettings(BaseModel):
    """The settings every spider has. A spider that needs more subclasses this."""

    model_config = ConfigDict(extra="forbid")

    visible_fields: list[str] = Field(default_factory=list, title="Fields shown in results")
    trial_max_requests: int = Field(default=5, ge=1, le=TRIAL_MAX, title="Trial run requests")
    max_requests: int | None = Field(
        default=None, ge=1, le=100_000, title="Max requests per run",
        description="Approximate when several requests run at once: those already started finish.",
    )
    request_delay: float | None = Field(default=None, ge=0, le=60, title="Delay between requests (seconds)")
    concurrency: int | None = Field(default=None, ge=1, le=8, title="Requests at once")

    @field_validator("visible_fields")
    @classmethod
    def _no_repeats(cls, names: list[str]) -> list[str]:
        for position, name in enumerate(names):
            if name in names[:position]:
                raise ValueError(f"{name} is listed twice")
        return names


COMMON_FIELDS = frozenset(SpiderSettings.model_fields)


class SettingsInvalid(ValueError):
    """The settings do not validate. `message` names the field and says what is wrong."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LocationProblem(ValueError):
    """A run cannot be started for the location asked for. `code` is the API's error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UnsupportedSetting(TypeError):
    """A spider declared a setting the page's form cannot draw."""


def explain(error: ValidationError) -> str:
    """The first problem, as `field.path: what is wrong`. Never the value: it may be long, or something that should not be echoed."""
    first = error.errors(include_url=False, include_context=False, include_input=False)[0]
    where = ".".join(str(part) for part in first["loc"])
    message = first["msg"].removeprefix("Value error, ")
    return f"{where}: {message}" if where else message


def item_fields(spider_cls: Any) -> list[str]:
    return list(spider_cls.item_schema.model_fields)


def settings_defaults(spider_cls: Any) -> dict:
    return spider_cls.settings_model().model_dump(mode="json")


def load_settings(spider_cls: Any, raw: dict | None) -> SpiderSettings:
    """Validate `raw` against the spider's settings model and its item fields. Nothing saved is every default."""
    try:
        settings = spider_cls.settings_model.model_validate(raw or {})
    except ValidationError as error:
        raise SettingsInvalid(explain(error)) from None
    known = item_fields(spider_cls)
    for name in settings.visible_fields:
        if name not in known:
            raise SettingsInvalid(f"visible_fields: {name} is not a field of this spider's items")
    return settings


def locations_of(spider_cls: Any, settings: SpiderSettings) -> list[dict]:
    """The entries a run can target, as plain dicts. Empty for a spider that has no locations."""
    if not spider_cls.locations_setting:
        return []
    return [entry.model_dump(mode="json") for entry in getattr(settings, spider_cls.locations_setting)]


def location_options(spider_cls: Any, settings: SpiderSettings) -> list[dict]:
    id_field, label_field = spider_cls.location_id_field, spider_cls.location_label_field
    return [{"id": entry[id_field], "label": entry[label_field]} for entry in locations_of(spider_cls, settings)]


def run_options(spider_cls: Any, settings: SpiderSettings, *, location_id: str | None, kind: str, max_requests: int | None = None) -> dict:
    """What a run is queued with: a snapshot of the settings, the whole chosen location (so every bound value is kept), and for a
    trial its request count."""
    options: dict = {"settings": settings.model_dump(mode="json")}
    entries = locations_of(spider_cls, settings)
    if spider_cls.locations_setting:
        if not entries:
            raise LocationProblem("no_locations", "Add a location in Settings to run this spider.")
        if location_id is None:
            raise LocationProblem("unknown_location", "Choose a location to run.")
        chosen = next((entry for entry in entries if entry[spider_cls.location_id_field] == location_id), None)
        if chosen is None:
            raise LocationProblem("unknown_location", "That location is not in this spider's settings.")
        options["location"] = chosen
    elif location_id is not None:
        raise LocationProblem("unknown_location", "This spider has no locations.")
    if kind == "trial":
        options["max_requests"] = max_requests or settings.trial_max_requests
    return options


# ---- describing a spider's own settings for the page's form --------------------------------------------------------------------


def _resolve(schema: dict, defs: dict) -> tuple[dict, bool]:
    """Follow a `$ref`, and turn "this type or none" into the type plus a `nullable` flag."""
    if "$ref" in schema:
        schema = {**schema, **defs[schema["$ref"].rsplit("/", 1)[-1]]}
        schema.pop("$ref", None)
    nullable = False
    if "anyOf" in schema:
        options = [option for option in schema["anyOf"] if option.get("type") != "null"]
        nullable = len(options) != len(schema["anyOf"])
        if len(options) != 1:
            raise UnsupportedSetting("a setting may be one type, or that type or none")
        inner, _ = _resolve(options[0], defs)
        schema = {**{key: value for key, value in schema.items() if key != "anyOf"}, **inner}
    return schema, nullable


def _describe(key: str, schema: dict, defs: dict, default: Any, nested: bool = False) -> dict:
    schema, nullable = _resolve(schema, defs)
    kind = schema.get("type")
    entry = {
        "key": key, "label": schema.get("title") or key, "description": schema.get("description"), "type": kind, "nullable": nullable,
        "default": default, "minimum": schema.get("minimum"), "maximum": schema.get("maximum"),
        "minLength": schema.get("minLength"), "maxLength": schema.get("maxLength"), "maxItems": schema.get("maxItems"),
    }
    if kind in SCALARS:
        return entry
    if kind == "array" and not nested:
        item, _ = _resolve(schema.get("items", {}), defs)
        if item.get("type") != "object":
            raise UnsupportedSetting(f"{key}: a list must hold records, not bare values")
        entry["items"] = [_describe(name, sub, defs, None, nested=True) for name, sub in item.get("properties", {}).items()]
        return entry
    raise UnsupportedSetting(f"{key}: a {kind or 'composite'} setting cannot be drawn in the form")


def describe_form(spider_cls: Any) -> list[dict]:
    """The spider's own settings (not the common ones, which the page draws itself), described for the form: key, label, type, range,
    default, and for a list the fields of each record. Raises `UnsupportedSetting` for a setting the form cannot draw."""
    model = spider_cls.settings_model
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    defaults = settings_defaults(spider_cls)
    return [
        _describe(key, prop, defs, defaults.get(key))
        for key, prop in schema.get("properties", {}).items()
        if key not in COMMON_FIELDS
    ]
