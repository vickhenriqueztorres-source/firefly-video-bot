from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from firefly_bot.config import Config
from firefly_bot.job_store import GuideValidationError, JobStore
from firefly_bot.main import build_parser
from firefly_bot.selectors import ACTION_SELECTORS
from firefly_bot.worker import Worker


def _make_pair(base: Path, name: str, suffix: str = ".png") -> Path:
    images = base / "imagens"
    prompts = images / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    image = images / f"{name}{suffix}"
    image.write_bytes(b"not-a-real-image")
    (prompts / f"{name}.txt").write_text(f"Anime {name}", encoding="utf-8")
    return image


def test_feed_explicit_guide_populates_batch_fields(tmp_path: Path) -> None:
    image = _make_pair(tmp_path, "produto1")
    guide = tmp_path / "guia_producao.json"
    guide.write_text(
        json.dumps(
            {
                "model": "Kling 3.0",
                "resolution": "720p",
                "aspect_ratio": "9:16",
                "duration_seconds": 15,
                "items": [
                    {
                        "image": image.name,
                        "prompt_file": "produto1.txt",
                        "name": "video-produto1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "queue.db")
    store.initialize()

    assert store.feed_from_guide(guide, tmp_path) == 1
    job = store.list_jobs()[0]
    assert job.prompt == "Anime produto1"
    assert job.image_path == str(image.resolve())
    assert job.model == "Kling 3.0"
    assert job.resolution == "720p"
    assert job.aspect_ratio == "9:16"
    assert job.duration_seconds == 15
    assert job.generate_audio is False
    assert job.name == "video-produto1"


def test_mixed_provider_guide_preserves_veo_audio_controls(tmp_path: Path) -> None:
    kling_image = _make_pair(tmp_path, "kling")
    veo_image = _make_pair(tmp_path, "veo")
    guide = tmp_path / "guia-misto.json"
    guide.write_text(
        json.dumps(
            {
                "model": "Kling 3.0",
                "resolution": "720p",
                "aspect_ratio": "16:9",
                "duration_seconds": 5,
                "generate_audio": False,
                "items": [
                    {"image": kling_image.name, "prompt": "physical scene", "name": "kling"},
                    {
                        "image": veo_image.name,
                        "prompt": "premium system motion",
                        "name": "veo",
                        "model": "Veo 3.1 Fast",
                        "duration_seconds": 8,
                        "generate_audio": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "queue.db")
    store.initialize()

    assert store.feed_from_guide(guide, tmp_path) == 2
    kling, veo = store.list_jobs()
    assert (kling.model, kling.duration_seconds, kling.generate_audio) == (
        "Kling 3.0",
        5,
        False,
    )
    assert (veo.model, veo.duration_seconds, veo.generate_audio) == (
        "Veo 3.1 Fast",
        8,
        True,
    )


def test_batch_guide_accepts_premium_veo31_model(tmp_path: Path) -> None:
    image = _make_pair(tmp_path, "premium-veo")
    guide = tmp_path / "guia-veo-premium.json"
    guide.write_text(
        json.dumps(
            {
                "model": "Veo 3.1",
                "resolution": "720p",
                "aspect_ratio": "16:9",
                "duration_seconds": 4,
                "items": [
                    {
                        "image": image.name,
                        "prompt": "premium documentary motion",
                        "name": "premium-veo",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "queue.db")
    store.initialize()

    assert store.feed_from_guide(guide, tmp_path) == 1
    job = store.list_jobs()[0]
    assert (job.model, job.duration_seconds, job.generate_audio) == ("Veo 3.1", 4, False)


def test_auto_discover_skips_image_without_prompt(tmp_path: Path) -> None:
    _make_pair(tmp_path, "com_prompt", ".jpg")
    (tmp_path / "imagens" / "sem_prompt.webp").write_bytes(b"image")
    store = JobStore(tmp_path / "queue.db")
    store.initialize()

    assert store.feed_auto_discover(tmp_path) == 1
    job = store.list_jobs()[0]
    assert job.name == "com_prompt"
    assert job.prompt == "Anime com_prompt"
    assert job.model == "Kling 2.5 Turbo"
    assert job.resolution == "1080p"
    assert job.aspect_ratio == "16:9"
    assert job.duration_seconds == 5


def test_guide_rejects_path_traversal(tmp_path: Path) -> None:
    (tmp_path / "imagens" / "prompts").mkdir(parents=True)
    guide = tmp_path / "guia.json"
    guide.write_text(
        json.dumps({"items": [{"image": "../segredo.png", "prompt": "x"}]}),
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "queue.db")
    store.initialize()

    with pytest.raises(GuideValidationError):
        store.feed_from_guide(guide, tmp_path)


def test_guide_rejects_unsafe_windows_output_name(tmp_path: Path) -> None:
    image = _make_pair(tmp_path, "produto")
    guide = tmp_path / "guia.json"
    guide.write_text(
        json.dumps(
            {"items": [{"image": image.name, "prompt": "x", "name": "../fora"}]}
        ),
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "queue.db")
    store.initialize()

    with pytest.raises(GuideValidationError):
        store.feed_from_guide(guide, tmp_path)


def test_initialize_migrates_existing_jobs_table(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                output_path TEXT,
                error TEXT,
                claimed_at REAL,
                updated_at REAL NOT NULL
            )
            """
        )
    store = JobStore(db_path)
    store.initialize()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert {
        "image_path",
        "generation_started_at",
        "model",
        "resolution",
        "aspect_ratio",
        "duration_seconds",
        "generate_audio",
        "name",
    } <= columns


def test_cli_accepts_batch_flags(tmp_path: Path) -> None:
    parser = build_parser()
    assert parser.parse_args(["--feed-guide", str(tmp_path / "guia.json")]).feed_guide
    assert parser.parse_args(["--feed-auto", str(tmp_path)]).feed_auto == tmp_path
    assert parser.parse_args(["--run"]).run is True
    assert parser.parse_args(["--run", "--concurrency", "3"]).concurrency == 3
    assert parser.parse_args(["run", "--concurrency", "3"]).concurrency == 3
    assert parser.parse_args(["--status"]).status is True


def test_guide_accepts_explicit_text_to_video_with_staged_reference(tmp_path: Path) -> None:
    images = tmp_path / "imagens"
    images.mkdir()
    (images / "SC_001.png").write_bytes(b"staged-reference")
    guide = tmp_path / "guide.json"
    guide.write_text(
        json.dumps({
            "model": "Firefly Video",
            "use_first_frame": False,
            "items": [{
                "name": "SC_001",
                "image": "SC_001.png",
                "use_first_frame": False,
                "prompt": "real documentary field operation",
                "duration_seconds": 5,
            }],
        }),
        encoding="utf-8",
    )
    db_path = tmp_path / "queue.db"
    store = JobStore(db_path)
    assert store.feed_from_guide(guide, tmp_path) == 1
    job = store.claim_next()
    assert job is not None
    assert job.image_path is None
    assert job.model == "Firefly Video"
    assert job.duration_seconds == 5


class _FakeLocator:
    def __init__(self) -> None:
        self.uploaded: str | None = None
        self.waited: tuple[str, int] | None = None

    async def set_input_files(self, image_path: str) -> None:
        self.uploaded = image_path

    async def wait_for(self, *, state: str, timeout: int) -> None:
        self.waited = (state, timeout)


class _FakePage:
    def __init__(self) -> None:
        self.locators: dict[str, _FakeLocator] = {}

    def locator(self, value: str) -> _FakeLocator:
        return self.locators.setdefault(value, _FakeLocator())


class _FakeHuman:
    async def click_element(self, _locator: object, _description: str) -> None:
        return None

    async def human_delay(self, _minimum: float, _maximum: float) -> None:
        return None


def test_first_frame_uses_file_input_and_positive_thumbnail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("first_frame_button", "first_frame_upload", "first_frame_thumbnail"):
        monkeypatch.setitem(
            ACTION_SELECTORS,
            key,
            replace(
                ACTION_SELECTORS[key], method="css", value=f"#{key}", confirmed=True, nth=None
            ),
        )
    page = _FakePage()
    worker = Worker(
        Config.from_root(tmp_path),
        JobStore(tmp_path / "queue.db"),
        logging.getLogger("test"),
    )
    worker._page = page  # type: ignore[assignment]
    worker._human = _FakeHuman()  # type: ignore[assignment]
    image = tmp_path / "frame.png"
    image.write_bytes(b"image")

    asyncio.run(worker._upload_first_frame(str(image)))

    assert page.locators["#first_frame_upload"].uploaded == str(image.resolve())
    assert page.locators["#first_frame_thumbnail"].waited == (
        "visible",
        5000,
    )


class _PickerLocator:
    def __init__(self, value: str) -> None:
        self.value = value

    async def get_attribute(self, name: str) -> str | None:
        return self.value if name == "value" else None


class _PickerTrigger:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    async def press(self, key: str) -> None:
        self.pressed.append(key)


class _PickerOption:
    def __init__(
        self, picker: _PickerLocator, selected_value: str, *, visible: bool = True
    ) -> None:
        self.picker = picker
        self.selected_value = selected_value
        self.visible = visible
        self.clicked = False
        self.waited: tuple[str, int] | None = None

    async def count(self) -> int:
        return int(self.visible)

    async def is_visible(self) -> bool:
        return self.visible

    async def wait_for(self, *, state: str, timeout: int) -> None:
        self.waited = (state, timeout)

    async def click(self) -> None:
        self.clicked = True
        self.picker.value = self.selected_value


class _DurationSlider:
    def __init__(self, minimum: int = 1, maximum: int = 15) -> None:
        self.minimum = minimum
        self.maximum = maximum
        self.value = minimum
        self.pressed: list[str] = []

    async def wait_for(self, *, state: str, timeout: int) -> None:
        assert state == "visible"
        assert timeout > 0

    async def get_attribute(self, name: str) -> str | None:
        return {
            "aria-valuemin": str(self.minimum),
            "aria-valuemax": str(self.maximum),
            "aria-valuenow": str(self.value),
            "value": str(self.value),
        }.get(name)

    async def press(self, key: str) -> None:
        self.pressed.append(key)
        if key == "Home":
            self.value = self.minimum
        elif key == "ArrowRight":
            self.value = min(self.maximum, self.value + 1)


def _worker_with_locators(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    locators: dict[str, object],
) -> Worker:
    worker = Worker(
        Config.from_root(tmp_path),
        JobStore(tmp_path / "queue.db"),
        logging.getLogger("picker-test"),
    )
    worker._human = _FakeHuman()  # type: ignore[assignment]
    monkeypatch.setattr(Worker, "_get_locator", lambda _self, key: locators[key])
    return worker


@pytest.mark.parametrize(
    ("configure_method", "argument", "picker_key", "trigger_key", "option_key", "selected"),
    [
        (
            "_configure_model",
            "Kling 3.0",
            "model_dropdown",
            "model_dropdown_trigger",
            "model_option_kling3",
            "kling:firefly:colligo:v3direct",
        ),
        (
            "_configure_aspect_ratio",
            "9:16",
            "aspect_ratio_dropdown",
            "aspect_ratio_dropdown_trigger",
            "aspect_ratio_vertical",
            '{"height":1280,"width":720}',
        ),
        (
            "_configure_aspect_ratio",
            "16:9",
            "aspect_ratio_dropdown",
            "aspect_ratio_dropdown_trigger",
            "aspect_ratio_widescreen",
            '{"height":720,"width":1280}',
        ),
    ],
)
def test_picker_configuration_opens_selects_and_verifies_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configure_method: str,
    argument: str,
    picker_key: str,
    trigger_key: str,
    option_key: str,
    selected: str,
) -> None:
    picker = _PickerLocator("old-value")
    trigger = _PickerTrigger()
    option = _PickerOption(picker, selected)
    worker = _worker_with_locators(
        tmp_path,
        monkeypatch,
        {picker_key: picker, trigger_key: trigger, option_key: option},
    )

    asyncio.run(getattr(worker, configure_method)(argument))

    assert trigger.pressed == ["Enter"]
    assert option.clicked is True
    assert picker.value == selected


def test_duration_uses_discrete_option_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    picker = _PickerLocator("8")
    trigger = _PickerTrigger()
    option = _PickerOption(picker, "5")
    worker = _worker_with_locators(
        tmp_path,
        monkeypatch,
        {
            "duration_dropdown": picker,
            "duration_dropdown_trigger": trigger,
            "duration_option_5": option,
        },
    )

    asyncio.run(worker._configure_duration(5))

    assert trigger.pressed == ["Enter"]
    assert option.clicked is True
    assert picker.value == "5"


def test_veo_duration_uses_discrete_picker_without_slider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    picker = _PickerLocator("8")
    trigger = _PickerTrigger()
    option = _PickerOption(picker, "6")
    worker = _worker_with_locators(
        tmp_path,
        monkeypatch,
        {
            "duration_dropdown": picker,
            "duration_dropdown_trigger": trigger,
            "duration_option_6": option,
        },
    )

    asyncio.run(worker._configure_duration(6, "Veo 3.1 Fast"))

    assert trigger.pressed == ["Enter"]
    assert option.clicked is True
    assert picker.value == "6"


def test_duration_moves_kling_slider_to_requested_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    picker = _PickerLocator("8")
    trigger = _PickerTrigger()
    missing_option = _PickerOption(picker, "10", visible=False)
    slider = _DurationSlider()
    worker = _worker_with_locators(
        tmp_path,
        monkeypatch,
        {
            "duration_dropdown": picker,
            "duration_dropdown_trigger": trigger,
            "duration_option_10": missing_option,
            "duration_slider": slider,
        },
    )

    asyncio.run(worker._configure_duration(10))

    assert trigger.pressed == ["Enter"]
    assert slider.value == 10
    assert slider.pressed == ["Home", *(["ArrowRight"] * 9), "Escape"]


def test_firefly_video_contract_is_fixed_at_five_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _worker_with_locators(tmp_path, monkeypatch, {})

    asyncio.run(worker._configure_duration(5, "Firefly Video"))

    with pytest.raises(ValueError, match="duração fixa de 5 segundos"):
        asyncio.run(worker._configure_duration(8, "Firefly Video"))


def test_kling_25_turbo_contract_is_fixed_at_five_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _worker_with_locators(tmp_path, monkeypatch, {})

    asyncio.run(worker._configure_duration(5, "Kling 2.5 Turbo"))

    with pytest.raises(ValueError, match="duracao fixa de 5 segundos|duração fixa de 5 segundos"):
        asyncio.run(worker._configure_duration(8, "Kling 2.5 Turbo"))


def test_guide_rejects_non_profile_frame_rate(tmp_path: Path) -> None:
    image = _make_pair(tmp_path, "fps_invalido")
    guide = tmp_path / "guia-fps.json"
    guide.write_text(
        json.dumps({"fps": 30, "items": [{"image": image.name, "prompt": "x"}]}),
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "queue.db")

    with pytest.raises(GuideValidationError, match="fps precisa ser 24"):
        store.feed_from_guide(guide, tmp_path)
