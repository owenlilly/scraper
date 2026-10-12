"""Request bodies (with their limits) and the JSON the API sends back."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from scraper.session import Session

SETTING_NAMES = ("url", "mode", "follow", "depth", "agent", "browser")  # form values kept between visits
RESUME_ACTIONS = ("continue", "wrap_up", "stop")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an unknown field is a mistake worth reporting


class ScrapeBody(Body):
    url: str = Field(min_length=1, max_length=2048)
    mode: Literal["fast", "dynamic", "stealthy"] = "fast"
    follow: bool = False
    depth: int = Field(default=1, ge=1, le=3)


class AskBody(Body):
    question: str = Field(min_length=1, max_length=8000)
    model: str = Field(min_length=1, max_length=200)


class RetryBody(Body):
    model: str = Field(min_length=1, max_length=200)


class RunBody(Body):
    instruction: str = Field(min_length=1, max_length=8000)
    model: str = Field(min_length=1, max_length=200)
    seed_url: str | None = Field(default=None, max_length=2048)


class ResumeBody(Body):
    """What a paused run is waiting for: an answer, a limit decision, or a yes/no for a new site."""

    value: str | dict

    @field_validator("value")
    @classmethod
    def _shape(cls, value):
        if isinstance(value, str):
            if not value.strip() or len(value) > 2000:
                raise ValueError("an answer must be 1 to 2000 characters")
            return value
        unknown = set(value) - {"action", "note", "allow"}
        if unknown:
            raise ValueError(f"unexpected key {sorted(unknown)[0]!r}")
        if "action" in value and value["action"] not in RESUME_ACTIONS:
            raise ValueError(f"action must be one of {', '.join(RESUME_ACTIONS)}")
        if "allow" in value and not isinstance(value["allow"], bool):
            raise ValueError("allow must be true or false")
        if "note" in value and (not isinstance(value["note"], str) or len(value["note"]) > 2000):
            raise ValueError("note must be text of at most 2000 characters")
        if "action" not in value and "allow" not in value:
            raise ValueError("give an action or allow")
        return value


class SteerBody(Body):
    text: str = Field(min_length=1, max_length=2000)


class SettingBody(Body):
    value: str = Field(max_length=2048)


class ProviderBody(Body):
    key: str = Field(min_length=1, max_length=40)


class ModelBody(Body):
    id: str = Field(min_length=1, max_length=200)


class DeleteAccountBody(Body):
    user_id: str = Field(min_length=1, max_length=200)


# ---- responses ----------------------------------------------------------------------


def page_summary(page, used: set[str]) -> dict:
    return {
        "url": page.url,
        "title": page.title,
        "chars": len(page.markdown),
        "links": len(page.links),
        "looks_empty": page.looks_empty,
        "used": page.url in used,
    }


def run_dict(session: Session) -> dict | None:
    run = session.run
    if run is None:
        return None
    return {
        "number": run.number,
        "instruction": run.instruction,
        "model": run.model,
        "status": run.status,
        "resumable": session.resumable,
        "pending": run.pending,
        "fetched": run.fetched,
        "max_pages": run.max_pages,
        "delay": run.delay,
        "rows_added": run.rows_added,
        "trace": run.trace,
        "followups": [{"id": f["id"], "text": f["text"], "delivered": f["delivered"]} for f in run.followups],
    }


def history_dict(message) -> dict:
    return {
        "role": message.role,
        "content": message.content,
        "reasoning": message.reasoning,
        "sources": list(message.sources),
        "interrupted": message.interrupted,
        "note": message.note,
        "trace": [{"name": t.name, "args": t.args, "text": t.text, "ok": t.ok} for t in message.trace],
    }


def model_dict(model) -> dict:
    return {"id": model.id, "context": model.context, "loaded": model.loaded}


def build_state(entry, models, models_error: str | None) -> dict:
    session = entry.session
    used = set(session.last_context.sources) if session.last_context else set()
    settings = {name: session.setting(name, "") for name in SETTING_NAMES}
    return {
        "user": {"id": entry.user_id},
        "provider": {"key": session.llm.key, "name": session.llm.name, "hosted": session.llm.hosted},
        "providers": [{"key": p.key, "name": p.name, "hosted": p.hosted} for p in session.providers.values()],
        "busy": session.busy,
        "pages": [page_summary(p, used) for p in session.pages],
        "failed": [{"url": url, "reason": why} for url, why in session.failed],
        "rows": session.rows,
        "history": [history_dict(m) for m in session.history],
        "run": run_dict(session),
        "progress": list(session.progress),
        "settings": settings,
        "model": session.preferred_model,
        "models": [model_dict(m) for m in models],
        "models_error": models_error,
        "fetch_modes": list(session.cfg.allowed_modes),
        "browser_available": session.cfg.browser,
        "usage": entry.meter.snapshot(),
    }
