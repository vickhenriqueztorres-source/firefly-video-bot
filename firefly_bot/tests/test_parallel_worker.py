from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

import firefly_bot.human_input as human_input_module
import firefly_bot.worker as worker_module
from firefly_bot.config import Config
from firefly_bot.human_input import HumanInput
from firefly_bot.job_store import Job, JobStore
from firefly_bot.state_reader import ScreenObservation, ScreenState
from firefly_bot.worker import WORKER_SUCCESS, Worker


class _FakePage:
    def __init__(self, page_id: int) -> None:
        self.page_id = page_id
        self.closed = False
        self.brought_to_front = False

    async def bring_to_front(self) -> None:
        self.brought_to_front = True

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self) -> None:
        self.bootstrap = _FakePage(0)
        self.pages = [self.bootstrap]
        self.created: list[_FakePage] = []
        self.closed = False
        self.init_script: str | None = None

    async def add_init_script(self, script: str) -> None:
        self.init_script = script

    async def new_page(self) -> _FakePage:
        page = _FakePage(len(self.created) + 1)
        self.created.append(page)
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, context: _FakeContext) -> None:
        self.context = context

    async def launch_persistent_context(self, **_kwargs: object) -> _FakeContext:
        return self.context


class _FakePlaywright:
    def __init__(self, context: _FakeContext) -> None:
        self.chromium = _FakeChromium(context)


class _FakePlaywrightManager:
    def __init__(self, context: _FakeContext) -> None:
        self.playwright = _FakePlaywright(context)

    async def __aenter__(self) -> _FakePlaywright:
        return self.playwright

    async def __aexit__(self, *_args: object) -> None:
        return None


def _parallel_config(tmp_path: Path) -> Config:
    config = Config.from_root(tmp_path)
    object.__setattr__(config, "TAB_START_STAGGER_SECONDS", 0.0)
    return config


def test_three_tabs_run_jobs_concurrently_and_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _parallel_config(tmp_path)
    store = JobStore(config.db_path)
    store.initialize()
    store.add_prompts(["job 1", "job 2", "job 3", "job 4"])
    context = _FakeContext()
    monkeypatch.setattr(worker_module, "close_existing_profile_chrome", lambda _path: [])
    monkeypatch.setattr(
        worker_module,
        "async_playwright",
        lambda: _FakePlaywrightManager(context),
    )

    active = 0
    maximum_active = 0
    all_started = asyncio.Event()
    job_pages: dict[int, int] = {}

    async def fake_run_claimed_page(
        self: Worker, job: Job, page: _FakePage
    ) -> int:
        nonlocal active, maximum_active
        await page.bring_to_front()
        active += 1
        maximum_active = max(maximum_active, active)
        job_pages[job.id] = page.page_id
        if active == 3:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1)
        self.store.transition(job.id, "claimed", "generating", generation_started=True)
        self.store.transition(job.id, "generating", "done", output_path=f"{job.id}.mp4")
        active -= 1
        return WORKER_SUCCESS

    monkeypatch.setattr(Worker, "_run_claimed_page", fake_run_claimed_page)
    worker = Worker(config, store, logging.getLogger("parallel-worker-test"))

    result = asyncio.run(worker.run_batch(3))

    assert result == WORKER_SUCCESS
    assert maximum_active == 3
    assert len(set(job_pages.values())) == 3
    assert all(page.brought_to_front for page in context.created)
    assert context.bootstrap.closed is True
    assert all(page.closed for page in context.created)
    assert context.closed is True
    assert [job.status for job in store.list_jobs()] == [
        "done",
        "done",
        "done",
        "pending",
    ]


def test_concurrency_is_bounded() -> None:
    config = Config()
    assert config.validate_concurrency(1) == 1
    assert config.validate_concurrency(3) == 3
    assert config.validate_concurrency(config.MAX_CONCURRENT_TABS) == 6
    with pytest.raises(ValueError):
        config.validate_concurrency(0)
    with pytest.raises(ValueError):
        config.validate_concurrency(7)


def test_polling_tolerates_transient_unknown_before_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _SequenceReader:
        def __init__(self) -> None:
            self.states = [ScreenState.UNKNOWN, ScreenState.RESULT_READY]

        async def read_screen_state(self, _job_id: int) -> ScreenObservation:
            return ScreenObservation(self.states.pop(0))

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr(worker_module.asyncio, "sleep", no_delay)
    config = Config.from_root(tmp_path)
    store = JobStore(config.db_path)
    store.initialize()
    store.add_prompts(["transição de tela"])
    job = store.claim_next()
    assert job is not None
    worker = Worker(config, store, logging.getLogger("transient-state-test"))

    observation = asyncio.run(worker._poll_generation(job, _SequenceReader()))

    assert observation.state is ScreenState.RESULT_READY


def test_prompt_is_filled_atomically(monkeypatch: pytest.MonkeyPatch) -> None:
    class _PromptLocator:
        value = ""

        async def fill(self, value: str) -> None:
            self.value = value

        async def press(self, _key: str) -> None:
            raise AssertionError("prompt não deve ser digitado tecla por tecla")

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr(human_input_module.asyncio, "sleep", no_delay)
    locator = _PromptLocator()

    asyncio.run(HumanInput(object()).type_prompt(locator, "prompt completo"))

    assert locator.value == "prompt completo"
