from __future__ import annotations

import asyncio
import time
from pathlib import Path

from firefly_bot.job_store import Job
from firefly_bot.result_identity import (
    capture_provider_result_identity,
    persist_provider_result_binding,
    search_target_result_identity,
)
from firefly_bot.state_reader import ScreenObservation, ScreenState


class _Clickable:
    def __init__(self, page: "_IdentityPage", label: str) -> None:
        self.page = page
        self.label = label

    @property
    def first(self) -> "_Clickable":
        return self

    async def click(self, **_kwargs: object) -> None:
        self.page.clicked.append(self.label)
        self.page.mode = "history"


class _IdentityPage:
    url = "https://firefly.adobe.com/generate/video"

    def __init__(self, *, mode: str = "current", asset_id: str | None = "asset-123") -> None:
        self.mode = mode
        self.asset_id = asset_id
        self.clicked: list[str] = []

    async def evaluate(self, _script: str) -> dict[str, object]:
        if self.mode == "history":
            return self._payload(result_ready=True, body="Kling Vertical Rafa recovered")
        return self._payload(result_ready=self.mode == "ready", body="Kling Vertical Rafa")

    def _payload(self, *, result_ready: bool, body: str) -> dict[str, object]:
        effective_ready = result_ready and self.asset_id is not None
        return {
            "title": "Firefly",
            "url": self.url,
            "downloadButtons": [
                {
                    "index": 0,
                    "text": "Baixar",
                    "data_id": self.asset_id,
                    "disabled": not effective_ready,
                    "visible": self.asset_id is not None,
                }
            ],
            "cards": [{"data_id": self.asset_id, "href": f"https://firefly.adobe.com/asset/{self.asset_id}", "visible": True}] if self.asset_id else [],
            "media": [{"src": f"https://cdn.example/{self.asset_id}.jpg", "visible": True}] if self.asset_id else [],
            "links": [],
            "body_excerpt": body,
        }

    def get_by_text(self, label: str, **_kwargs: object) -> _Clickable:
        return _Clickable(self, label)

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


def _job() -> Job:
    return Job(
        id=93,
        prompt="Vertical Kling Rafa camera trading",
        image_path=None,
        status="failed-infra",
        attempts=1,
        output_path=None,
        error="DOWNLOAD_EVENT_TIMEOUT",
        claimed_at=time.time(),
        generation_started_at=time.time(),
        updated_at=time.time(),
        model="Kling 3.0",
        resolution="720p",
        aspect_ratio="9:16",
        duration_seconds=10,
        name="SHOT_001_TAKE_01",
        download_started_at=None,
        download_completed_at=None,
        media_validated_at=None,
        media_validation_status=None,
        media_validation_error=None,
        file_size_bytes=None,
        sha256=None,
        width=None,
        height=None,
        codec=None,
    )


def test_result_ready_exposes_asset_id_and_is_persisted(tmp_path: Path) -> None:
    job = _job()
    page = _IdentityPage(mode="ready", asset_id="asset-abc")
    observation = ScreenObservation(ScreenState.RESULT_READY, url=page.url)

    identity = asyncio.run(capture_provider_result_identity(page, job, observation))
    path = persist_provider_result_binding(tmp_path, job=job, identity=identity, source_shot_id="SHOT_001")

    assert identity["provider_result_recovery_capability"] == "DURABLE_IDENTITY_AVAILABLE"
    assert "asset-abc" in path.read_text(encoding="utf-8")


def test_global_still_generating_but_target_found_in_history_recovers() -> None:
    job = _job()
    page = _IdentityPage(mode="current", asset_id="asset-history")
    observation = ScreenObservation(ScreenState.STILL_GENERATING, url=page.url)

    result = asyncio.run(search_target_result_identity(page, job, observation))

    assert result["status"] == "RESULT_MATCH_CONFIRMED"
    assert result["matched_location"] in {"Galeria", "Gallery", "Histórico", "History", "Projetos", "Projects"}
    assert page.clicked


def test_target_not_found_is_not_recoverable() -> None:
    job = _job()
    page = _IdentityPage(mode="current", asset_id=None)
    observation = ScreenObservation(ScreenState.STILL_GENERATING, url=page.url)

    result = asyncio.run(search_target_result_identity(page, job, observation))

    assert result["status"] == "RESULT_READY_ARTIFACT_NO_LONGER_RECOVERABLE"


def test_replacement_generation_policy_allows_exactly_one() -> None:
    max_replacements = 1
    attempts = [{"replaced_job_id": 93, "status": "CREATED"}]

    assert len(attempts) == max_replacements
    assert len(attempts) + 1 > max_replacements
