from contextlib import AsyncExitStack

from fakes import FakeLLM, ScriptedChat, ai, call, make_fetch, make_page
from scraper.config import Config
from scraper.usage import MemoryUsageStore
from scraper.api.accounts import delete_user_data
from scraper.api.db import open_checkpointer
from scraper.api.registry import SessionRegistry

A = "https://site.test/a"
CFG = Config(hosted=True, request_delay=0.0)


def registry_with(pg, saver, script):
    model = ScriptedChat(script=list(script))
    registry = SessionRegistry(
        CFG, pool=pg, checkpointer=saver, llm=FakeLLM(), usage_store=MemoryUsageStore(),
        fetch_base=make_fetch({A: make_page(A)}), url_check=lambda url: None, model_factory=lambda model_id: model,
    )
    return registry, model


def thread_ids(pg):
    with pg.connection() as conn:
        return sorted(r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM app.checkpoints"))


async def test_a_paused_run_survives_a_restart_and_its_checkpoints_belong_to_the_user(pg, pg_url):
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(pg_url, stack)
        delete_user_data(pg, "alice")  # a clean slate even if an earlier run was interrupted
        first, _ = registry_with(pg, saver, [ai("", call("ask_user", "c1", question="Which plan?"))])
        entry = await first.get("alice")
        await _drain(entry.session.run_agent("pick a plan", "qwen-test"))
        assert entry.session.run.status == "paused"
        assert thread_ids(pg) and all(t.startswith("alice:") for t in thread_ids(pg))

        # the server restarts: nothing is in memory, everything is in Postgres
        second, _ = registry_with(pg, saver, [ai("Starter it is.")])
        restored = (await second.get("alice")).session
        assert restored.run.status == "paused" and restored.pending["question"] == "Which plan?"
        events = await _drain(restored.resume_run("Starter"))
        assert events[-1].kind == "done" and restored.run.status == "done"
        delete_user_data(pg, "alice")


async def test_a_run_that_lost_its_checkpoint_is_ended_not_resumed(pg, pg_url):
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(pg_url, stack)
        delete_user_data(pg, "alice")
        first, _ = registry_with(pg, saver, [ai("", call("ask_user", "c1", question="Which?"))])
        entry = await first.get("alice")
        await _drain(entry.session.run_agent("x", "qwen-test"))
        with pg.connection() as conn:
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                conn.execute(f"DELETE FROM app.{table} WHERE starts_with(thread_id, 'alice:')")
        second, _ = registry_with(pg, saver, [])
        assert (await second.get("alice")).session.run.status == "ended"
        delete_user_data(pg, "alice")


async def test_deleting_an_account_removes_that_users_checkpoints_and_no_one_elses(pg, pg_url):
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(pg_url, stack)
        for user in ("alice", "bob"):
            delete_user_data(pg, user)
        registry, model = registry_with(pg, saver, [])
        for user in ("alice", "bob"):
            model.script = [ai("", call("ask_user", "c1", question="Which?"))]
            await _drain((await registry.get(user)).session.run_agent("x", "qwen-test"))
        assert {t.split(":")[0] for t in thread_ids(pg)} == {"alice", "bob"}
        delete_user_data(pg, "alice")
        assert {t.split(":")[0] for t in thread_ids(pg)} == {"bob"}
        delete_user_data(pg, "bob")
        assert thread_ids(pg) == []


async def test_the_checkpoint_tables_live_in_the_app_schema_only(pg, pg_url):
    async with AsyncExitStack() as stack:
        await open_checkpointer(pg_url, stack)
        with pg.connection() as conn:
            schemas = {r[0] for r in conn.execute("SELECT table_schema FROM information_schema.tables WHERE table_name = 'checkpoints'")}
    assert schemas == {"app"}


def test_deleting_data_before_the_checkpointer_ever_ran_does_not_fail(pg):
    delete_user_data(pg, "nobody")  # the checkpoint tables may not exist yet on a fresh database


async def _drain(stream):
    return [event async for event in stream]
