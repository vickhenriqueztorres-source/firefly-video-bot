"""Durable identity evidence for Firefly/Kling results."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .job_store import Job
from .state_reader import ScreenObservation, ScreenState


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def capture_provider_result_identity(
    page: object,
    job: Job,
    observation: ScreenObservation,
) -> dict[str, Any]:
    """Capture only provider identity fields that are observable in the real UI."""
    payload = await page.evaluate(
        """() => {
            const attr = (el, name) => el ? el.getAttribute(name) : null;
            const visible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
            const downloadButtons = Array.from(document.querySelectorAll('[data-testid="generate-video-download-button"], button, [role="button"]'))
              .filter((el) => /baixar|download/i.test(el.textContent || el.getAttribute('aria-label') || '') || attr(el, 'data-testid') === 'generate-video-download-button')
              .map((el, index) => ({
                index,
                text: (el.textContent || '').trim(),
                aria_label: attr(el, 'aria-label'),
                data_testid: attr(el, 'data-testid'),
                data_id: attr(el, 'data-id') || attr(el, 'data-asset-id') || attr(el, 'data-generation-id'),
                disabled: el.hasAttribute('disabled') || attr(el, 'aria-disabled') === 'true',
                visible: visible(el)
              }));
            const cards = Array.from(document.querySelectorAll('[data-id], [data-asset-id], [data-generation-id], a[href*="firefly"], a[href*="asset"], a[href*="generation"]'))
              .slice(0, 100)
              .map((el, index) => ({
                index,
                tag: el.tagName,
                text: (el.textContent || '').trim().slice(0, 500),
                href: el.href || attr(el, 'href'),
                data_id: attr(el, 'data-id'),
                asset_id: attr(el, 'data-asset-id'),
                generation_id: attr(el, 'data-generation-id'),
                role: attr(el, 'role'),
                aria_label: attr(el, 'aria-label'),
                visible: visible(el)
              }));
            const media = Array.from(document.querySelectorAll('video, img')).slice(0, 100).map((el, index) => ({
              index,
              tag: el.tagName,
              src: el.currentSrc || el.src || attr(el, 'src'),
              poster: attr(el, 'poster'),
              alt: attr(el, 'alt'),
              data_id: attr(el, 'data-id') || attr(el, 'data-asset-id') || attr(el, 'data-generation-id'),
              visible: visible(el)
            }));
            const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 100).map((el, index) => ({
              index,
              href: el.href,
              text: (el.textContent || '').trim().slice(0, 200),
              aria_label: attr(el, 'aria-label')
            }));
            return {
              title: document.title,
              url: location.href,
              downloadButtons,
              cards,
              media,
              links,
              body_excerpt: document.body ? document.body.innerText.slice(0, 3000) : ''
            };
        }"""
    )
    dom_fingerprint_source = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    start_frame_path = Path(job.image_path).resolve() if job.image_path else None
    identity = {
        "schema_version": "1.0",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mission_control_job_id": job.id,
        "provider_job_id": job.id,
        "job_name": job.name,
        "model": job.model,
        "prompt_hash": f"sha256_{sha256_text(job.prompt)}",
        "start_frame_path": str(start_frame_path) if start_frame_path else None,
        "start_frame_sha256": f"sha256_{sha256_file(start_frame_path)}" if start_frame_path and sha256_file(start_frame_path) else None,
        "generation_started_at": job.generation_started_at,
        "result_ready_at": time.time() if observation.state is ScreenState.RESULT_READY else None,
        "current_canvas_state": observation.state.value,
        "current_url": observation.url,
        "selectors_found": list(observation.selectors_found),
        "provider_observed": _compact_provider_payload(payload),
        "dom_fingerprint": f"sha256_{sha256_text(dom_fingerprint_source)}",
    }
    identity["provider_result_recovery_capability"] = (
        "DURABLE_IDENTITY_AVAILABLE"
        if _has_durable_provider_identity(identity["provider_observed"])
        else "LIMITED"
    )
    return identity


async def search_target_result_identity(
    page: object,
    job: Job,
    observation: ScreenObservation,
) -> dict[str, Any]:
    """Finite target-result search. Never clicks Generate."""
    searches: list[dict[str, Any]] = []
    current = await capture_provider_result_identity(page, job, observation)
    searches.append({"location": "current_canvas", "identity": current})
    if _current_canvas_matches_target(current, job):
        return {
            "status": "RESULT_MATCH_CONFIRMED",
            "target_result_state": "result_ready",
            "matched_location": "current_canvas",
            "searches": searches,
            "provider_result_identity": current,
        }

    for label in ("Galeria", "Gallery", "Histórico", "History", "Projetos", "Projects"):
        try:
            button = page.get_by_text(label, exact=False).first
            await button.click(timeout=2000)
            await page.wait_for_timeout(1200)
            gallery = await capture_provider_result_identity(page, job, observation)
            searches.append({"location": label, "identity": gallery})
            if _current_canvas_matches_target(gallery, job):
                return {
                    "status": "RESULT_MATCH_CONFIRMED",
                    "target_result_state": "result_ready",
                    "matched_location": label,
                    "searches": searches,
                    "provider_result_identity": gallery,
                }
        except Exception as exc:
            searches.append({"location": label, "error": type(exc).__name__})

    return {
        "status": "RESULT_READY_ARTIFACT_NO_LONGER_RECOVERABLE",
        "target_result_state": "not_found",
        "searches": searches,
        "provider_result_identity": current,
    }


def persist_provider_result_binding(
    output_dir: Path,
    *,
    job: Job,
    identity: dict[str, Any],
    source_production_id: str | None = None,
    source_shot_id: str | None = None,
    motion_package_sha256: str | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    binding = {
        "schema_version": "1.0",
        "source_production_id": source_production_id,
        "source_shot_id": source_shot_id,
        "mission_control_job_id": job.id,
        "provider_job_id": job.id,
        "provider_result_identity": identity,
        "prompt_hash": identity.get("prompt_hash"),
        "start_frame_sha256": identity.get("start_frame_sha256"),
        "motion_package_sha256": motion_package_sha256,
        "generation_started_at": job.generation_started_at,
        "result_ready_at": identity.get("result_ready_at"),
    }
    path = output_dir / "provider_result_identity.json"
    path.write_text(json.dumps(binding, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _compact_provider_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in ("title", "url", "downloadButtons", "cards", "media", "links", "body_excerpt")
        if payload.get(key) not in (None, [], "")
    }


def _has_durable_provider_identity(payload: dict[str, Any]) -> bool:
    for collection in ("cards", "media", "links"):
        for item in payload.get(collection, []) or []:
            if item.get("data_id") or item.get("asset_id") or item.get("generation_id") or item.get("href") or item.get("src"):
                return True
    return False


def _current_canvas_matches_target(identity: dict[str, Any], job: Job) -> bool:
    observed = identity.get("provider_observed", {})
    buttons = observed.get("downloadButtons", []) or []
    ready_download = any(item.get("visible") and not item.get("disabled") for item in buttons)
    body = str(observed.get("body_excerpt") or "")
    prompt_terms_match = _prompt_terms_match(job.prompt, body)
    return ready_download and prompt_terms_match


def _prompt_terms_match(prompt: str, body: str) -> bool:
    if not body.strip():
        return True
    important = [
        token.lower()
        for token in ("Kling", "Vertical", "Rafa", "camera", "café", "cafe", "trading")
        if token.lower() in prompt.lower()
    ]
    if not important:
        return True
    body_lower = body.lower()
    return any(token in body_lower for token in important)
