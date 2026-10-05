from __future__ import annotations

from pathlib import Path

from firefly_bot.worker import resolve_native_file_chooser_helper


def test_isolated_runtime_falls_back_to_packaged_native_helper(tmp_path: Path) -> None:
    helper = resolve_native_file_chooser_helper(tmp_path / "isolated-runtime")

    assert helper.name == "native_file_chooser.ps1"
    assert helper.is_file()
    assert helper.parent.name == "scripts"


def test_worker_does_not_delete_firefly_asset_caches_before_upload() -> None:
    worker_source = (Path(__file__).resolve().parents[1] / "worker.py").read_text(
        encoding="utf-8"
    )

    assert "caches.delete" not in worker_source
    assert "first_frame_asset_cache_preserved" in worker_source
