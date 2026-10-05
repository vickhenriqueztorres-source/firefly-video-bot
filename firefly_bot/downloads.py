"""Physical media validation before any transition to done."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class DownloadValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedDownload:
    path: Path
    file_size_bytes: int
    sha256: str
    width: int
    height: int
    duration_seconds: float
    codec: str
    ffprobe: dict[str, object]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wait_for_stable_file(path: Path, *, checks: int = 2, delay_seconds: float = 1.0) -> None:
    previous_size = -1
    for _ in range(checks + 1):
        if not path.is_file():
            raise DownloadValidationError(f"download does not exist: {path}")
        current_size = path.stat().st_size
        if current_size == previous_size:
            return
        previous_size = current_size
        time.sleep(delay_seconds)
    raise DownloadValidationError(f"FAILED_MEDIA_VALIDATION: download still changing: {path}")


def _reject_text_error_payload(path: Path) -> None:
    head = path.read_bytes()[:512]
    text = head.decode("utf-8", errors="ignore").lstrip()
    if text.startswith("<!DOCTYPE html") or text.startswith("<html"):
        raise DownloadValidationError("FAILED_MEDIA_VALIDATION: HTML_RESPONSE_SAVED_AS_MP4")
    if text.startswith('{"error"') or text.startswith("{'error'"):
        raise DownloadValidationError("FAILED_MEDIA_VALIDATION: JSON_ERROR_SAVED_AS_MP4")
    if "AccessDenied" in text or "Unauthorized" in text:
        raise DownloadValidationError("FAILED_MEDIA_VALIDATION: UNAUTHORIZED_RESPONSE_SAVED_AS_MP4")


def _validate_container_signature(path: Path) -> None:
    head = path.read_bytes()[:16]
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov"} and (len(head) < 12 or head[4:8] != b"ftyp"):
        raise DownloadValidationError("FAILED_MEDIA_VALIDATION: INVALID_CONTAINER_SIGNATURE")
    if suffix == ".webm" and not head.startswith(b"\x1a\x45\xdf\xa3"):
        raise DownloadValidationError("FAILED_MEDIA_VALIDATION: INVALID_CONTAINER_SIGNATURE")


def _ffprobe(path: Path) -> dict[str, object]:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise DownloadValidationError(
            f"FAILED_MEDIA_VALIDATION: ffprobe exit code={proc.returncode} stderr={proc.stderr.strip()}"
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DownloadValidationError("FAILED_MEDIA_VALIDATION: invalid ffprobe JSON") from exc
    streams = payload.get("streams") if isinstance(payload, dict) else None
    video_streams = [
        stream for stream in streams or [] if isinstance(stream, dict) and stream.get("codec_type") == "video"
    ]
    if not video_streams:
        raise DownloadValidationError("FAILED_MEDIA_VALIDATION: missing video stream")
    stream = video_streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    codec = str(stream.get("codec_name") or "")
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    duration = float(fmt.get("duration") or stream.get("duration") or 0)
    if width <= 0 or height <= 0:
        raise DownloadValidationError("FAILED_MEDIA_VALIDATION: invalid dimensions")
    if duration <= 0:
        raise DownloadValidationError("FAILED_MEDIA_VALIDATION: invalid duration")
    if not codec:
        raise DownloadValidationError("FAILED_MEDIA_VALIDATION: missing codec")
    payload["_validated_video"] = {
        "width": width,
        "height": height,
        "duration_seconds": duration,
        "codec": codec,
        "video_stream_count": len(video_streams),
    }
    return payload


def validate_and_move_download(
    temporary_path: Path,
    downloads_dir: Path,
    job_id: int,
    suggested_filename: str,
    min_file_size_bytes: int,
) -> ValidatedDownload:
    """Validate the .part file with ffprobe, then atomically publish it."""
    if not temporary_path.is_file():
        raise DownloadValidationError(f"download does not exist: {temporary_path}")
    _wait_for_stable_file(temporary_path)
    size = temporary_path.stat().st_size
    if size <= min_file_size_bytes:
        raise DownloadValidationError(
            f"FAILED_MEDIA_VALIDATION: download too small bytes={size} minimum={min_file_size_bytes}"
        )
    _reject_text_error_payload(temporary_path)
    _validate_container_signature(temporary_path)
    ffprobe_payload = _ffprobe(temporary_path)
    validated = ffprobe_payload["_validated_video"]
    sha256 = _sha256(temporary_path)
    filename = Path(suggested_filename).name
    if Path(filename).suffix.lower() not in {".mp4", ".mov", ".webm"}:
        raise DownloadValidationError(f"FAILED_MEDIA_VALIDATION: unsupported video extension: {filename}")
    downloads_dir.mkdir(parents=True, exist_ok=True)
    destination = downloads_dir / f"{job_id}_{filename}"
    if temporary_path.parent.resolve() != downloads_dir.resolve():
        raise DownloadValidationError(
            "FAILED_MEDIA_VALIDATION: temporary file must be on the destination filesystem"
        )
    if destination.exists():
        raise DownloadValidationError(f"FAILED_MEDIA_VALIDATION: destination already exists: {destination}")
    temporary_path.replace(destination)
    if not os.path.isfile(destination):
        raise DownloadValidationError(f"FAILED_MEDIA_VALIDATION: final move failed: {destination}")
    return ValidatedDownload(
        path=destination,
        file_size_bytes=size,
        sha256=sha256,
        width=int(validated["width"]),
        height=int(validated["height"]),
        duration_seconds=float(validated["duration_seconds"]),
        codec=str(validated["codec"]),
        ffprobe=ffprobe_payload,
    )
