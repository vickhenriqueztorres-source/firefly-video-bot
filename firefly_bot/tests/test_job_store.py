from __future__ import annotations

from datetime import UTC, datetime, timedelta

from firefly_bot.job_store import JobStore


def build_store(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    return store


def test_claim_is_atomic_and_increments_attempts(tmp_path) -> None:
    store = build_store(tmp_path)
    assert store.add_prompts(["primeiro", "segundo"]) == 2

    first = store.claim_next()
    second = store.claim_next()
    third = store.claim_next()

    assert first is not None and first.prompt == "primeiro" and first.attempts == 1
    assert second is not None and second.prompt == "segundo" and second.attempts == 1
    assert third is None


def test_reconciliation_preserves_active_generation_as_stale(tmp_path) -> None:
    store = build_store(tmp_path)
    store.add_prompts(["ainda ativa", "expirada", "somente claimed"])
    active = store.claim_next()
    expired = store.claim_next()
    claimed = store.claim_next()
    assert active and expired and claimed
    store.transition(active.id, "claimed", "generating", generation_started=True)
    store.transition(expired.id, "claimed", "generating", generation_started=True)

    now = datetime.now(UTC)
    with (
        store._connect() as conn
    ):  # Ajuste de relógio controlado para verificar o contrato de reconciliação.
        conn.execute(
            "UPDATE jobs SET generation_started_at=? WHERE id=?",
            ((now - timedelta(seconds=601)).isoformat(timespec="seconds"), expired.id),
        )

    result = store.reconcile(600, now=now)
    jobs = {job.id: job for job in store.list_jobs()}

    assert result == {"claimed_reset": 1, "expired_reset": 1, "stale": 1}
    assert jobs[active.id].status == "stale_generating"
    assert jobs[expired.id].status == "pending"
    assert jobs[claimed.id].status == "pending"


def test_done_transition_requires_expected_status(tmp_path) -> None:
    store = build_store(tmp_path)
    store.add_prompts(["vídeo"])
    job = store.claim_next()
    assert job is not None
    store.transition(job.id, "claimed", "generating", generation_started=True)
    store.transition(job.id, "generating", "done", output_path="downloads/1_video.mp4")

    saved = store.list_jobs()[0]
    assert saved.status == "done"
    assert saved.output_path == "downloads/1_video.mp4"


def test_manual_requeue_accepts_only_unstarted_infra_failure(tmp_path) -> None:
    store = build_store(tmp_path)
    store.add_prompts(["upload falhou"])
    job = store.claim_next()
    assert job is not None
    store.transition(job.id, "claimed", "generating", generation_started=False)
    store.transition(job.id, "generating", "failed-infra", error="upload")

    store.requeue_unstarted_infra_failure(job.id)

    saved = store.get_job(job.id)
    assert saved is not None
    assert saved.status == "pending"
    assert saved.generation_started_at is None
    assert saved.output_path is None
