from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from firefly_bot.config import Config
from firefly_bot.job_store import (
    claim_job,
    feed_prompts,
    get_status,
    init_db,
    mark_done,
    mark_generating,
    reconcile_jobs,
)
from firefly_bot.selectors import STATE_SELECTORS
from firefly_bot.state_reader import ScreenState, read_screen_state


def test_config_exposes_only_sprint_one_fields() -> None:
    assert Config().GENERATION_BUDGET == 1_800_000
    assert Config().MAX_ATTEMPTS == 3
    assert Config().UNKNOWN_THRESHOLD == 3
    assert Config().MAX_GENERATIONS_PER_HOUR == 20
    assert Config().firefly_url == "https://firefly.adobe.com/generate/video"


def test_functional_store_claims_and_reconciles(tmp_path) -> None:
    conn = init_db(Config(DB_PATH=str(tmp_path / "jobs.db")))
    try:
        assert feed_prompts(conn, ["ativa", "expirada", "apenas claim"]) == 3
        active = claim_job(conn)
        expired = claim_job(conn)
        claimed = claim_job(conn)
        assert active and expired and claimed
        mark_generating(conn, int(active["id"]))
        mark_generating(conn, int(expired["id"]))
        conn.execute(
            "UPDATE jobs SET generation_started_at=? WHERE id=?",
            (time.time() - 601, expired["id"]),
        )

        result = reconcile_jobs(conn, generation_budget_seconds=600)
        jobs = {row["id"]: row for row in conn.execute("SELECT * FROM jobs")}

        assert result == {"claimed_reset": 1, "expired_reset": 1, "stale": 1}
        assert jobs[active["id"]]["status"] == "stale_generating"
        assert jobs[expired["id"]]["status"] == "pending"
        assert jobs[claimed["id"]]["status"] == "pending"
        assert get_status(conn)["pending"] == 2
    finally:
        conn.close()


def test_done_requires_generating_state(tmp_path) -> None:
    conn = init_db(Config(DB_PATH=str(tmp_path / "jobs.db")))
    try:
        feed_prompts(conn, ["vídeo"])
        job = claim_job(conn)
        assert job is not None
        mark_generating(conn, int(job["id"]))
        mark_done(conn, int(job["id"]), "downloads/1_video.mp4")
        saved = conn.execute("SELECT status, output_path FROM jobs").fetchone()
        assert saved["status"] == "done"
        assert saved["output_path"] == "downloads/1_video.mp4"
    finally:
        conn.close()


class FakeLocator:
    async def is_visible(self, *, timeout: int) -> bool:
        assert timeout == 500
        return True


class FakePage:
    url = "https://example.test/generate"

    def get_by_role(self, role: str, **kwargs: object) -> FakeLocator:
        return FakeLocator()

    def get_by_text(self, text: str, **kwargs: object) -> FakeLocator:
        return FakeLocator()

    def get_by_label(self, text: str, **kwargs: object) -> FakeLocator:
        return FakeLocator()

    def get_by_test_id(self, test_id: str) -> FakeLocator:
        return FakeLocator()

    def locator(self, selector: str, **kwargs: object) -> FakeLocator:
        return FakeLocator()

    async def screenshot(self, *, path: str, full_page: bool) -> None:
        raise AssertionError("Screenshot é responsabilidade do Worker, não do Sprint 1")


class SelectiveFakePage(FakePage):
    def get_by_role(self, role: str, **kwargs: object) -> FakeLocator:
        if kwargs.get("name") == "Download":
            return FakeLocator()
        class InvisibleLocator:
            async def is_visible(self, *, timeout: int) -> bool:
                return False
        return InvisibleLocator()  # type: ignore[return-value]

    def get_by_text(self, text: str, **kwargs: object) -> FakeLocator:
        class InvisibleLocator:
            async def is_visible(self, *, timeout: int) -> bool:
                return False
        return InvisibleLocator()  # type: ignore[return-value]


def test_state_reader_skips_unconfirmed_and_uses_priority() -> None:
    previous = dict(STATE_SELECTORS)
    try:
        STATE_SELECTORS[ScreenState.RESULT_READY] = replace(
            STATE_SELECTORS[ScreenState.RESULT_READY],
            method="role",
            value="button:Download",
            confirmed=True,
        )
        assert asyncio.run(read_screen_state(SelectiveFakePage())) is ScreenState.RESULT_READY
    finally:
        STATE_SELECTORS.clear()
        STATE_SELECTORS.update(previous)
