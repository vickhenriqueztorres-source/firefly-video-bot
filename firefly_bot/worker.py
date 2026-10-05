"""Executor de uma onda de jobs em abas isoladas de um único Chrome."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import traceback
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

if os.environ.get("FIREFLY_BROWSER_DRIVER") == "playwright":
    from playwright.async_api import Error as PatchrightError
    from playwright.async_api import TimeoutError as PatchrightTimeoutError
    from playwright.async_api import async_playwright
else:
    from patchright.async_api import Error as PatchrightError
    from patchright.async_api import TimeoutError as PatchrightTimeoutError
    from patchright.async_api import async_playwright

from .chrome_profile import ChromeProfileBusyError, close_existing_profile_chrome
from .config import Config
from .duration_control import DurationController
from .dom_text import deep_composed_text
from .export_flow import export_video, export_video_from_provider_url
from .generation_profile import GENERATION_PROFILE
from .human_input import HumanInput
from .job_store import ConcurrentTransitionError, Job, JobStore
from .logging_utils import event
from .overlays import dismiss_overlays
from .result_identity import (
    capture_provider_result_identity,
    persist_provider_result_binding,
    search_target_result_identity,
)
from .selectors import ACTION_SELECTORS, UnconfirmedSelectorError, locator_for
from .session import SessionManager, SessionNotAuthenticatedError
from .state_reader import ScreenObservation, ScreenState, StateReader

WORKER_SUCCESS = 0
WORKER_NO_JOB = 10
WORKER_PAUSED = 20
WORKER_FAILED = 30

MODEL_PICKER_OPTIONS: dict[str, tuple[str, str]] = {
    "firefly video": ("adobe:firefly:colligo:video1", "Firefly Video"),
    "veo 3.1": ("ugs:video:veo@3.1-generate", "Veo 3.1"),
    "veo 3.1 fast": ("ugs:video:veo@3.1-fast-generate", "Veo 3.1 Fast"),
    "gemini omni flash": ("ugs:video:gemini-omni@omni-flash", "Gemini Omni Flash"),
    "kling 3.0": ("kling:firefly:colligo:v3direct", "Kling 3.0"),
    "kling 3.0 omni": ("kling:firefly:colligo:o3direct", "Kling 3.0 Omni"),
    "seedance 2.0": ("ugs:video:seedance@seedance_2.0", "Seedance 2.0"),
    "seedance 2.0 fast": ("ugs:video:seedance@seedance_2.0_fast", "Seedance 2.0 Fast"),
    "runway gen-4.5": ("ugs:video:runway@gen4.5", "Runway Gen-4.5"),
    "ray3.14": ("ugs:video:luma@3.14-ray", "Ray3.14"),
    "ray3.14 hdr": ("ugs:video:luma@3.14-ray-hdr", "Ray3.14 HDR"),
    "ray3": ("ugs:video:luma@3.0-ray", "Ray3"),
    "kling 2.5 turbo": ("ugs:video:kling@kling_v2_5_turbo_pro_i2v", "Kling 2.5 Turbo"),
    "ray2": ("ugs:video:luma@2.0-ray", "Ray2"),
    "ray3 hdr": ("ugs:video:luma@3.0-ray-hdr", "Ray3 HDR"),
}

MODEL_DEFAULT_DURATION_MODELS = {
    "firefly video",
    "gemini omni flash",
    "runway gen-4.5",
    "ray3.14",
    "ray3.14 hdr",
    "ray3",
    "ray3 hdr",
    "ray2",
}

FIXED_FIVE_SECOND_MODELS = {"firefly video", "kling 2.5 turbo"}


def resolve_native_file_chooser_helper(runtime_root: Path) -> Path:
    """Resolve o helper empacotado mesmo quando a fila usa runtime isolado."""
    package_root = Path(__file__).resolve().parents[1]
    candidates = (
        runtime_root / "scripts" / "native_file_chooser.ps1",
        package_root / "scripts" / "native_file_chooser.ps1",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    rendered = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(
        f"native file chooser helper ausente; caminhos verificados: {rendered}"
    )


class QueuePausedError(RuntimeError):
    """A fila já foi devolvida/pausada de modo atômico e não deve sofrer nova transição."""


@dataclass(slots=True)
class Worker:
    config: Config
    store: JobStore
    logger: logging.Logger
    _page: object | None = field(init=False, default=None, repr=False)
    _human: HumanInput | None = field(init=False, default=None, repr=False)
    _current_job_id: int | None = field(init=False, default=None, repr=False)
    _slot_id: int | None = field(init=False, default=None, repr=False)
    _foreground_lock: asyncio.Lock | None = field(init=False, default=None, repr=False)
    _provider_network_failures: list[dict[str, object]] = field(
        init=False, default_factory=list, repr=False
    )
    _provider_result_urls: dict[int, str] = field(
        init=False, default_factory=dict, repr=False
    )
    _provider_result_metadata: dict[int, dict[str, object]] = field(
        init=False, default_factory=dict, repr=False
    )
    _provider_capacity_overlay_seen: bool = field(init=False, default=False, repr=False)

    async def run_once(self) -> int:
        """Compatibilidade com o executor serial original."""
        return await self.run_batch(1)

    async def recover_result_ready_job(self, job_id: int) -> int:
        """Recover a previously generated RESULT_READY job by exporting only."""
        job = self.store.get_job(job_id)
        if job is None:
            event(self.logger, logging.ERROR, "recovery_job_missing", job_id=job_id)
            return WORKER_FAILED
        if job.status != "failed-infra":
            event(
                self.logger,
                logging.ERROR,
                "recovery_job_status_invalid",
                job_id=job_id,
                status=job.status,
            )
            return WORKER_FAILED
        if not job.generation_started_at or job.output_path:
            event(
                self.logger,
                logging.ERROR,
                "recovery_precondition_failed",
                job_id=job_id,
                generation_started_at=job.generation_started_at,
                output_path=job.output_path,
            )
            return WORKER_FAILED
        try:
            closed_processes = close_existing_profile_chrome(self.config.profile_dir)
        except ChromeProfileBusyError as exc:
            self.store.set_system_status("paused-profile", str(exc))
            event(self.logger, logging.ERROR, "chrome_profile_busy", error=str(exc))
            return WORKER_PAUSED
        if closed_processes:
            event(self.logger, logging.WARNING, "existing_bot_chrome_closed", process_ids=closed_processes)
        try:
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.config.profile_dir),
                    channel=self.config.chrome_channel,
                    headless=False,
                    no_viewport=True,
                    args=[
                        "--disable-http2",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-background-timer-throttling",
                        "--disable-backgrounding-occluded-windows",
                        "--disable-renderer-backgrounding",
                    ],
                    ignore_default_args=["--enable-automation"],
                )
                page = await context.new_page()
                try:
                    await context.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                    )
                    return await self._recover_result_ready_page(job, page)
                finally:
                    try:
                        await context.close()
                    except PatchrightError:
                        pass
        except Exception as exc:
            await self._fail_infrastructure(job, exc)
            return WORKER_FAILED

    async def run_batch(self, concurrency: int = 1) -> int:
        """Executa até ``concurrency`` jobs em páginas independentes do mesmo perfil."""
        concurrency = self.config.validate_concurrency(concurrency)
        self.store.initialize()
        reconciliation = self.store.reconcile(self.config.generation_budget_seconds)
        event(self.logger, logging.INFO, "reconciliation", **reconciliation)
        if self.store.get_system_status() != "running":
            event(
                self.logger, logging.WARNING, "worker_paused", status=self.store.get_system_status()
            )
            return WORKER_PAUSED
        try:
            closed_processes = close_existing_profile_chrome(self.config.profile_dir)
        except ChromeProfileBusyError as exc:
            self.store.set_system_status("paused-profile", str(exc))
            event(self.logger, logging.ERROR, "chrome_profile_busy", error=str(exc))
            return WORKER_PAUSED
        if closed_processes:
            event(
                self.logger,
                logging.WARNING,
                "existing_bot_chrome_closed",
                process_ids=closed_processes,
            )
        jobs = self._claim_batch(concurrency)
        if not jobs:
            event(self.logger, logging.INFO, "queue_empty")
            return WORKER_NO_JOB

        try:
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.config.profile_dir),
                    channel=self.config.chrome_channel,
                    headless=False,
                    no_viewport=True,
                    args=[
                        "--disable-http2",
                        "--disable-blink-features=AutomationControlled",
                        # O Chrome reduz timers/renderização de abas em segundo plano.
                        # O pool depende de todas as páginas continuarem responsivas.
                        "--disable-background-timer-throttling",
                        "--disable-backgrounding-occluded-windows",
                        "--disable-renderer-backgrounding",
                    ],
                    ignore_default_args=["--enable-automation"],
                )
                try:
                    await context.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                    )
                    return await self._run_claimed_batch(context, jobs)
                finally:
                    try:
                        await context.close()
                    except PatchrightError as exc:
                        event(
                            self.logger,
                            logging.WARNING,
                            "browser_context_already_closed",
                            error=type(exc).__name__,
                        )
        except (PatchrightTimeoutError, PatchrightError, RuntimeError, OSError) as exc:
            self._return_unstarted_jobs(jobs, exc)
            event(
                self.logger,
                logging.ERROR,
                "batch_browser_failed",
                jobs=[job.id for job in jobs],
                error=f"{type(exc).__name__}: {exc}",
            )
            return WORKER_FAILED

    def _claim_batch(self, concurrency: int) -> list[Job]:
        jobs: list[Job] = []
        for slot_id in range(1, concurrency + 1):
            job = self.store.claim_next()
            if job is None:
                break
            jobs.append(job)
            event(
                self.logger,
                logging.INFO,
                "slot_claimed",
                slot_id=slot_id,
                job_id=job.id,
            )
        return jobs

    async def _run_claimed_batch(self, context: object, jobs: list[Job]) -> int:
        # O perfil pode restaurar páginas de uma execução encerrada à força. Cada
        # job recebe uma página nova e identificável; as páginas restauradas são fechadas.
        restored_pages = list(context.pages)
        pages = [await context.new_page() for _ in jobs]
        for restored_page in restored_pages:
            try:
                await restored_page.close()
            except PatchrightError:
                pass

        event(
            self.logger,
            logging.INFO,
            "parallel_batch_started",
            concurrency=len(jobs),
            jobs=[job.id for job in jobs],
        )
        tasks = []
        foreground_lock = asyncio.Lock()
        for slot_id, (job, page) in enumerate(zip(jobs, pages, strict=True), start=1):
            slot_worker = Worker(self.config, self.store, self.logger)
            slot_worker._slot_id = slot_id
            slot_worker._foreground_lock = foreground_lock
            tasks.append(
                asyncio.create_task(
                    self._run_page_slot(slot_worker, job, page),
                    name=f"firefly-slot-{slot_id}-job-{job.id}",
                )
            )
        results = await asyncio.gather(*tasks)
        event(
            self.logger,
            logging.INFO,
            "parallel_batch_finished",
            results=results,
            jobs=[job.id for job in jobs],
        )
        if WORKER_PAUSED in results:
            return WORKER_PAUSED
        if WORKER_FAILED in results:
            return WORKER_FAILED
        return WORKER_SUCCESS

    async def _run_page_slot(self, worker: Worker, job: Job, page: object) -> int:
        slot_id = worker._slot_id
        delay = max(0.0, (slot_id or 1) - 1) * self.config.tab_start_stagger_seconds
        try:
            if delay:
                await asyncio.sleep(delay)
            if self.store.get_system_status() != "running":
                self.store.return_to_pending(
                    job.id, "claimed", "lote pausado antes de iniciar esta aba"
                )
                return WORKER_PAUSED
            event(
                self.logger,
                logging.INFO,
                "slot_started",
                slot_id=slot_id,
                job_id=job.id,
                start_delay_seconds=delay,
            )
            # O timeout por aba impede uma chamada CDP presa de manter todas as
            # outras páginas aguardando até o watchdog matar o Chrome inteiro.
            return await asyncio.wait_for(
                worker._run_claimed_page(job, page),
                timeout=max(30, self.config.watchdog_wall_clock_seconds - 30),
            )
        except TimeoutError:
            await worker._fail_infrastructure(
                job, RuntimeError(f"slot {slot_id} excedeu o timeout de parede")
            )
            return WORKER_FAILED
        except Exception as exc:  # última contenção: uma aba nunca cancela as demais
            await worker._fail_infrastructure(job, exc)
            return WORKER_FAILED
        finally:
            try:
                await page.close()
            except PatchrightError:
                pass

    async def _run_claimed_page(self, job: Job, page: object) -> int:
        """Executa um job numa página que pertence exclusivamente a esta instância."""
        try:
            self._page = page
            self._current_job_id = job.id
            async with self._foreground_page(page):
                if self.store.get_system_status() != "running":
                    self.store.return_to_pending(
                        job.id, "claimed", "fila pausada antes da preparação da aba"
                    )
                    return WORKER_PAUSED
                event(
                    self.logger,
                    logging.INFO,
                    "slot_preparation_started",
                    slot_id=self._slot_id,
                    job_id=job.id,
                )
                page.set_default_timeout(self.config.selector_timeout_ms)
                await page.goto(
                    self.config.firefly_url,
                    wait_until="domcontentloaded",
                    timeout=self.config.nav_timeout_ms,
                )
                state_reader = StateReader(page, self.config.screenshots_dir, self.logger, self.config)
                await SessionManager(page, state_reader, self.config).require_authenticated(
                    job.id
                )
                await self._start_generation(job, page)
                event(
                    self.logger,
                    logging.INFO,
                    "slot_preparation_finished",
                    slot_id=self._slot_id,
                    job_id=job.id,
                )

            await self._complete_generation(job, page, state_reader)
            return WORKER_SUCCESS
        except SessionNotAuthenticatedError as exc:
            self.store.return_to_pending(job.id, "claimed", str(exc))
            self.store.set_system_status("paused-auth", str(exc))
            event(
                self.logger,
                logging.WARNING,
                "paused_auth",
                slot_id=self._slot_id,
                job_id=job.id,
                error=str(exc),
            )
            return WORKER_PAUSED
        except QueuePausedError as exc:
            event(self.logger, logging.WARNING, "queue_paused", job_id=job.id, error=str(exc))
            return WORKER_PAUSED
        except ConcurrentTransitionError as exc:
            event(
                self.logger, logging.ERROR, "concurrent_transition", job_id=job.id, error=str(exc)
            )
            return WORKER_FAILED
        except UnconfirmedSelectorError as exc:
            self._pause_for_unconfirmed_selector(job, exc)
            return WORKER_PAUSED
        except Exception as exc:
            await self._fail_infrastructure(job, exc)
            return WORKER_FAILED

    async def _recover_result_ready_page(self, job: Job, page: object) -> int:
        self._page = page
        self._human = HumanInput(page)
        self._current_job_id = job.id
        page.set_default_timeout(self.config.selector_timeout_ms)
        await page.goto(
            self.config.firefly_url,
            wait_until="domcontentloaded",
            timeout=self.config.nav_timeout_ms,
        )
        state_reader = StateReader(page, self.config.screenshots_dir, self.logger, self.config)
        await SessionManager(page, state_reader, self.config).require_authenticated(job.id)
        observation = await state_reader.read_screen_state(job.id)
        artifact_dir = Path(os.environ.get("FIREFLY_DIAG_DIR", str(self.config.screenshots_dir)))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        search = await search_target_result_identity(page, job, observation)
        (artifact_dir / f"job_{job.id}_target_result_search.json").write_text(
            json.dumps(search, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if search.get("status") != "RESULT_MATCH_CONFIRMED":
            (artifact_dir / f"job_{job.id}_recovery_not_recoverable.json").write_text(
                json.dumps(
                    {
                        "job_id": job.id,
                        "status": "RESULT_READY_ARTIFACT_NO_LONGER_RECOVERABLE",
                        "observed_state": observation.state.value,
                        "target_result_state": search.get("target_result_state"),
                        "url": observation.url,
                        "generate_clicked": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            event(
                self.logger,
                logging.ERROR,
                "result_ready_artifact_no_longer_recoverable",
                job_id=job.id,
                observed_state=observation.state.value,
            )
            return WORKER_FAILED
        previous_error = job.error
        recovery_started_at = time.time()
        identity = search.get("provider_result_identity")
        if isinstance(identity, dict):
            identity_path = persist_provider_result_binding(
                artifact_dir,
                job=job,
                identity=identity,
                source_shot_id=(job.name or "").split("_TAKE_", maxsplit=1)[0] or None,
            )
            event(
                self.logger,
                logging.INFO,
                "provider_result_identity_persisted",
                job_id=job.id,
                path=str(identity_path),
                recovery=True,
            )
        event(
            self.logger,
            logging.INFO,
            "export_recovery_started",
            job_id=job.id,
            previous_status=job.status,
            previous_error=previous_error,
            recovery_reason="RESULT_READY_WITH_EXPORT_TIMEOUT",
            generate_clicked=False,
        )
        validated_download = await export_video(
            page,
            self._require_human(),
            self.config,
            self.logger,
            job.id,
        )
        published_path = self._publish_batch_output(validated_download.path, job)
        validation_time = time.time()
        self.store.transition(
            job.id,
            "failed-infra",
            "done",
            error="INFRA_EXPORT_RECOVERY",
            output_path=str(published_path),
            media_metadata={
                "download_started_at": recovery_started_at,
                "download_completed_at": validation_time,
                "media_validated_at": validation_time,
                "media_validation_status": "PASS",
                "media_validation_error": None,
                "file_size_bytes": validated_download.file_size_bytes,
                "sha256": validated_download.sha256,
                "width": validated_download.width,
                "height": validated_download.height,
                "codec": validated_download.codec,
            },
        )
        artifact_dir = Path(os.environ.get("FIREFLY_DIAG_DIR", str(self.config.screenshots_dir)))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        lineage = {
            "recovery_attempt_id": f"FIREFLY-EXPORT-RECOVERY-001-job-{job.id}",
            "recovered_from_job_id": job.id,
            "previous_status": "failed-infra",
            "previous_error": previous_error,
            "previous_error_classification": "DOWNLOAD_EVENT_TIMEOUT",
            "recovery_reason": "RESULT_READY_WITH_EXPORT_TIMEOUT",
            "generate_clicked": False,
            "status": "INFRA_EXPORT_RECOVERY",
            "output_path": str(published_path),
            "sha256": validated_download.sha256,
            "file_size_bytes": validated_download.file_size_bytes,
            "width": validated_download.width,
            "height": validated_download.height,
            "duration_seconds": validated_download.duration_seconds,
            "codec": validated_download.codec,
        }
        (artifact_dir / f"job_{job.id}_export_recovery_lineage.json").write_text(
            json.dumps(lineage, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        event(
            self.logger,
            logging.INFO,
            "export_recovery_done",
            job_id=job.id,
            output_path=published_path,
            sha256=validated_download.sha256,
        )
        return WORKER_SUCCESS

    @asynccontextmanager
    async def _foreground_page(self, page: object):
        """Serializa ações que o Firefly só materializa na aba visível."""
        if self._foreground_lock is None:
            await page.bring_to_front()
            yield
            return
        async with self._foreground_lock:
            await page.bring_to_front()
            yield

    def _return_unstarted_jobs(self, jobs: list[Job], exc: Exception) -> None:
        reason = f"browser não iniciou o lote: {type(exc).__name__}: {exc}"
        for job in jobs:
            try:
                self.store.return_to_pending(job.id, "claimed", reason)
            except ConcurrentTransitionError:
                # O job pode ter terminado antes de uma falha no fechamento do contexto.
                continue

    def _pause_for_unconfirmed_selector(
        self, job: Job, exc: UnconfirmedSelectorError
    ) -> None:
        reason = str(exc)
        try:
            self.store.return_to_pending(job.id, "claimed", reason)
        except ConcurrentTransitionError:
            self.store.transition(job.id, "generating", "failed-infra", error=reason)
        self.store.set_system_status("paused-selectors", reason)
        event(
            self.logger,
            logging.WARNING,
            "paused_unconfirmed_selector",
            job_id=job.id,
            error=reason,
        )

    async def _start_generation(self, job: Job, page: object) -> None:
        """Prepara e inicia a geração enquanto esta página detém o primeiro plano."""
        human_input = HumanInput(page)
        self._page = page
        self._human = human_input
        self._current_job_id = job.id

        await self._open_video_generation()
        self._install_provider_network_diagnostics(job, page)
        # Coachmarks podem nascer junto com o compositor e bloquear todos os
        # controles seguintes, principalmente o upload do primeiro quadro.
        await dismiss_overlays(page, human_input, self.logger, job.id)
        await self._configure_model(job.model)
        await dismiss_overlays(page, human_input, self.logger, job.id)
        await self._configure_resolution(job.resolution)
        await self._configure_aspect_ratio(job.aspect_ratio)
        await self._verify_frame_rate(int(GENERATION_PROFILE["fps"]))
        await self._configure_audio(job.generate_audio)
        if job.image_path:
            await self._upload_first_frame(job.image_path)
        elif os.environ.get("FIREFLY_ALLOW_TEXT_TO_VIDEO") == "true":
            event(
                self.logger,
                logging.INFO,
                "text_to_video_mode_confirmed",
                job_id=job.id,
                model=job.model,
            )
        else:
            raise RuntimeError(
                f"job_id={job.id} não possui image_path e texto-para-vídeo não foi autorizado"
            )

        await dismiss_overlays(page, human_input, self.logger, job.id)
        prompt_input = locator_for(page, "prompt_input")
        await human_input.click(prompt_input)
        await human_input.type_prompt(prompt_input, job.prompt, page=page)

        await dismiss_overlays(page, human_input, self.logger, job.id)
        await self._configure_duration(job.duration_seconds, job.model)
        # O popover de duração pode permanecer sobre o prompt. Fechá-lo deixa o
        # clique de geração e os controles seguintes em um estado determinístico.
        await page.keyboard.press("Escape")
        await self._ensure_single_capture_mode(job, page)
        await dismiss_overlays(page, human_input, self.logger, job.id)
        settle_seconds = float(os.environ.get("FIREFLY_PRE_GENERATION_SETTLE_SECONDS", "0") or 0)
        if settle_seconds > 0:
            event(
                self.logger,
                logging.INFO,
                "pre_generation_settle_started",
                job_id=job.id,
                seconds=settle_seconds,
            )
            await asyncio.sleep(settle_seconds)
        await self._capture_pre_generation_snapshot(job, page)
        await self._audit_credit_spend(job, page)
        await self._click_generate_and_confirm(job, page, human_input)
        self.store.transition(job.id, "claimed", "generating", generation_started=True)
        event(self.logger, logging.INFO, "generation_started", job_id=job.id, state="generating")

    async def _audit_credit_spend(self, job: Job, page: object) -> None:
        credit_info = await page.evaluate(
            """() => {
                const seen = new Set();
                const texts = [];
                function walk(root) {
                    if (!root || seen.has(root)) return;
                    seen.add(root);
                    const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                    for (const el of elements) {
                        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (/cr[eé]ditos|credits/i.test(text)) texts.push(text);
                        if (el.shadowRoot) walk(el.shadowRoot);
                    }
                }
                walk(document);
                const joined = texts.join(' | ');
                const match = joined.match(/(?:Usa|Uses)\\s+(\\d+)\\s+(?:cr[eé]ditos|credits)/i);
                return {texts, cost: match ? Number(match[1]) : 0};
            }"""
        )
        cost = int(credit_info.get("cost") or 0)
        if cost <= 0:
            return
        if os.environ.get("FIREFLY_ALLOW_CREDIT_SPEND") != "true":
            raise RuntimeError(
                f"FIREFLY_CREDIT_SPEND_NOT_AUTHORIZED: job_id={job.id} cost={cost}"
            )
        event(
            self.logger,
            logging.INFO,
            "credit_spend_authorized",
            job_id=job.id,
            model=job.model,
            cost_credits=cost,
            authorization="FIREFLY_ALLOW_CREDIT_SPEND=true",
        )

    async def _ensure_single_capture_mode(self, job: Job, page: object) -> None:
        """Desliga o modo de várias capturas para preservar o Start Frame exato."""
        result = await page.evaluate(
            """() => {
                const seen = new Set();
                function walk(root) {
                    if (!root || seen.has(root)) return null;
                    seen.add(root);
                    const direct = root.querySelector && root.querySelector(
                        '[data-testid="generate-video-multi-shot-toggle"]'
                    );
                    if (direct) return direct;
                    const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                    for (const el of elements) {
                        const found = el.shadowRoot ? walk(el.shadowRoot) : null;
                        if (found) return found;
                    }
                    return null;
                }
                const toggle = walk(document);
                if (!toggle) return {found: false, before: null, after: null, changed: false};
                const readChecked = () => Boolean(
                    toggle.checked === true ||
                    toggle.getAttribute('checked') !== null ||
                    toggle.getAttribute('aria-checked') === 'true' ||
                    toggle.getAttribute('aria-pressed') === 'true'
                );
                const before = readChecked();
                if (before) toggle.click();
                return {found: true, before, after: readChecked(), changed: before};
            }"""
        )
        if not result.get("found"):
            event(
                self.logger,
                logging.WARNING,
                "single_capture_toggle_not_found",
                job_id=job.id,
            )
            return
        await asyncio.sleep(0.35)
        verified = await page.evaluate(
            """() => {
                const seen = new Set();
                function walk(root) {
                    if (!root || seen.has(root)) return null;
                    seen.add(root);
                    const direct = root.querySelector && root.querySelector(
                        '[data-testid="generate-video-multi-shot-toggle"]'
                    );
                    if (direct) return direct;
                    const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                    for (const el of elements) {
                        const found = el.shadowRoot ? walk(el.shadowRoot) : null;
                        if (found) return found;
                    }
                    return null;
                }
                const toggle = walk(document);
                if (!toggle) return null;
                return Boolean(
                    toggle.checked === true ||
                    toggle.getAttribute('checked') !== null ||
                    toggle.getAttribute('aria-checked') === 'true' ||
                    toggle.getAttribute('aria-pressed') === 'true'
                );
            }"""
        )
        if verified is True:
            raise RuntimeError(
                f"FIREFLY_MULTI_CAPTURE_COULD_NOT_BE_DISABLED: job_id={job.id}"
            )
        event(
            self.logger,
            logging.INFO,
            "single_capture_mode_verified",
            job_id=job.id,
            was_enabled=bool(result.get("before")),
        )

    async def _capture_pre_generation_snapshot(self, job: Job, page: object) -> Path:
        """Registra o contrato real exibido pela UI imediatamente antes do gasto."""
        base_dir = Path(os.environ.get("FIREFLY_DIAG_DIR", str(self.config.screenshots_dir)))
        artifact_dir = base_dir / "provider" / "pre_generation"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        payload = await page.evaluate(
            """() => {
                const seen = new Set();
                const nodes = [];
                function isVisible(el) {
                    if (!el || !el.getBoundingClientRect) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return (rect.width > 0 || rect.height > 0) &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                }
                function walk(root, path) {
                    if (!root || seen.has(root)) return;
                    seen.add(root);
                    const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                    for (const el of elements) {
                        const testId = el.getAttribute && el.getAttribute('data-testid');
                        const ariaLabel = el.getAttribute && el.getAttribute('aria-label');
                        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (testId || ariaLabel || /cr[eé]ditos|credits|ocorreu um erro/i.test(text)) {
                            nodes.push({
                                path,
                                tag: el.tagName,
                                testId,
                                ariaLabel,
                                text: text.slice(0, 500),
                                value: el.value ?? el.getAttribute?.('value') ?? null,
                                checked: typeof el.checked === 'boolean' ? el.checked : null,
                                ariaChecked: el.getAttribute?.('aria-checked') ?? null,
                                ariaPressed: el.getAttribute?.('aria-pressed') ?? null,
                                disabled: Boolean(el.disabled || el.hasAttribute?.('disabled')),
                                ariaDisabled: el.getAttribute?.('aria-disabled') ?? null,
                                visible: isVisible(el)
                            });
                        }
                        if (el.shadowRoot) walk(el.shadowRoot, `${path}>${el.tagName.toLowerCase()}#shadow`);
                    }
                }
                walk(document, 'document');
                return {title: document.title, url: location.href, capturedAt: new Date().toISOString(), nodes};
            }"""
        )
        payload.update(
            {
                "job_id": job.id,
                "job_name": job.name,
                "requested_model": job.model,
                "requested_resolution": job.resolution,
                "requested_aspect_ratio": job.aspect_ratio,
                "requested_duration_seconds": job.duration_seconds,
                "requested_generate_audio": bool(job.generate_audio),
                "image_path": job.image_path,
            }
        )
        json_path = artifact_dir / f"job_{job.id}_pre_generation.json"
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        screenshot_path = artifact_dir / f"job_{job.id}_pre_generation.png"
        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
        except (PatchrightTimeoutError, PatchrightError) as exc:
            event(
                self.logger,
                logging.WARNING,
                "pre_generation_screenshot_failed",
                job_id=job.id,
                error=type(exc).__name__,
            )
        event(
            self.logger,
            logging.INFO,
            "pre_generation_snapshot_captured",
            job_id=job.id,
            path=str(json_path),
        )
        return json_path

    def _install_provider_network_diagnostics(self, job: Job, page: object) -> None:
        """Registra falhas XHR/fetch do provider sem persistir query strings ou tokens."""
        base_dir = Path(os.environ.get("FIREFLY_DIAG_DIR", str(self.config.screenshots_dir)))
        artifact_dir = base_dir / "provider" / "network"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        target = artifact_dir / f"job_{job.id}_network.jsonl"

        def append_record(record: dict[str, object]) -> None:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        def safe_response_body(raw_body: str) -> object:
            """Preserva estado/erro do job sem gravar URLs assinadas ou credenciais."""
            try:
                payload = json.loads(raw_body)
            except json.JSONDecodeError:
                return {"non_json_response_bytes": len(raw_body.encode("utf-8"))}

            sensitive_markers = (
                "authorization",
                "credential",
                "cookie",
                "download",
                "signed",
                "token",
                "url",
            )

            def redact(value: object, key: str = "") -> object:
                key_folded = key.casefold()
                if any(marker in key_folded for marker in sensitive_markers):
                    return "<redacted>"
                if isinstance(value, dict):
                    return {str(k): redact(v, str(k)) for k, v in value.items()}
                if isinstance(value, list):
                    return [redact(item) for item in value[:8]]
                if isinstance(value, str):
                    if value.casefold().startswith(("http://", "https://")):
                        return "<redacted-url>"
                    return value[:1000]
                return value

            return redact(payload)

        async def capture_response(response: object) -> None:
            try:
                request = response.request
                resource_type = str(getattr(request, "resource_type", ""))
                if resource_type not in {"xhr", "fetch"}:
                    return
                raw_url = str(getattr(response, "url", ""))
                if not any(
                    marker in raw_url.casefold()
                    for marker in ("adobe", "firefly", "firefall", "colligo", "veo")
                ):
                    return
                status = int(getattr(response, "status", 0) or 0)
                body = None
                diagnostic_success = any(
                    marker in raw_url.casefold()
                    for marker in ("/v2/jobs/result/", "/v2/videos/generate")
                )
                if status >= 400 or diagnostic_success:
                    try:
                        raw_body = (await response.text())[:100_000]
                        if "/v2/jobs/result/" in raw_url.casefold():
                            try:
                                provider_payload = json.loads(raw_body)
                            except json.JSONDecodeError:
                                provider_payload = None
                            if isinstance(provider_payload, dict):
                                outputs = provider_payload.get("outputs")
                                first_output = (
                                    outputs[0]
                                    if isinstance(outputs, list) and outputs and isinstance(outputs[0], dict)
                                    else None
                                )
                                video = (
                                    first_output.get("video")
                                    if isinstance(first_output, dict)
                                    and isinstance(first_output.get("video"), dict)
                                    else None
                                )
                                provider_url = video.get("presignedUrl") if video else None
                                if isinstance(provider_url, str) and provider_url.startswith("https://"):
                                    self._provider_result_urls[job.id] = provider_url
                                    self._provider_result_metadata[job.id] = {
                                        "video_id": video.get("id"),
                                        "progress": provider_payload.get("progress"),
                                        "size": provider_payload.get("size"),
                                    }
                                    event(
                                        self.logger,
                                        logging.INFO,
                                        "provider_api_result_captured",
                                        job_id=job.id,
                                        video_id=video.get("id"),
                                        progress=provider_payload.get("progress"),
                                        has_presigned_url=True,
                                    )
                        body = safe_response_body(raw_body)
                    except (PatchrightTimeoutError, PatchrightError):
                        body = "RESPONSE_BODY_UNAVAILABLE"
                from urllib.parse import urlsplit, urlunsplit

                parsed = urlsplit(raw_url)
                safe_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
                record = {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "kind": "response",
                    "method": str(getattr(request, "method", "")),
                    "resource_type": resource_type,
                    "status": status,
                    "url": safe_url,
                    "body": body,
                }
                if status >= 400:
                    self._provider_network_failures.append(record)
                append_record(record)
            except Exception as exc:
                event(
                    self.logger,
                    logging.WARNING,
                    "provider_network_response_capture_failed",
                    job_id=job.id,
                    error=type(exc).__name__,
                )

        def on_response(response: object) -> None:
            asyncio.create_task(capture_response(response))

        def on_request_failed(request: object) -> None:
            try:
                from urllib.parse import urlsplit, urlunsplit

                raw_url = str(getattr(request, "url", ""))
                parsed = urlsplit(raw_url)
                safe_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
                record = {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "kind": "request_failed",
                    "method": str(getattr(request, "method", "")),
                    "resource_type": str(getattr(request, "resource_type", "")),
                    "url": safe_url,
                    "failure": str(getattr(request, "failure", "")),
                }
                append_record(record)
            except Exception as exc:
                event(
                    self.logger,
                    logging.WARNING,
                    "provider_network_request_capture_failed",
                    job_id=job.id,
                    error=type(exc).__name__,
                )

        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)
        page.on(
            "console",
            lambda message: append_record({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "kind": "console",
                "type": str(getattr(message, "type", "")),
                "text": str(getattr(message, "text", ""))[:4000],
            }) if str(getattr(message, "type", "")) in {"error", "warning"} else None,
        )
        page.on(
            "pageerror",
            lambda error: append_record({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "kind": "pageerror",
                "text": str(error)[:4000],
            }),
        )
        event(
            self.logger,
            logging.INFO,
            "provider_network_diagnostics_installed",
            job_id=job.id,
            path=str(target),
        )

    def _latest_retryable_provider_failure(self) -> dict[str, object] | None:
        for failure in reversed(self._provider_network_failures):
            status = int(failure.get("status") or 0)
            if status in {408, 429, 500, 502, 503, 504}:
                return failure
        return None

    async def _retry_after_provider_capacity_error(
        self, job: Job, state: str
    ) -> bool:
        failure = self._latest_retryable_provider_failure()
        ui_capacity = self._provider_capacity_overlay_seen
        if failure is None and not ui_capacity:
            return False
        status = int(failure.get("status") or 0) if failure is not None else 429
        capacity_max_attempts = max(
            self.config.MAX_ATTEMPTS,
            int(os.environ.get("FIREFLY_PROVIDER_CAPACITY_MAX_ATTEMPTS", "12")),
        )
        if job.attempts >= capacity_max_attempts:
            event(
                self.logger,
                logging.ERROR,
                "provider_capacity_retry_exhausted",
                job_id=job.id,
                attempt=job.attempts,
                max_attempts=capacity_max_attempts,
                status=status,
                state=state,
                url=failure.get("url") if failure is not None else None,
                body=failure.get("body") if failure is not None else "Firefly UI capacity overlay",
            )
            return False
        if status in {408, 429}:
            # Capacidade e rate limit precisam de um circuit breaker real. Repetir
            # em 30 s apenas devolve a mesma carga ao provider.
            backoff_seconds = min(600, 300 * max(1, job.attempts))
        else:
            backoff_seconds = min(180, 60 * max(1, job.attempts))
        reason = (
            f"provider capacity {'UI slow-down' if ui_capacity else f'HTTP {status}'}; "
            f"retry {job.attempts + 1}/{capacity_max_attempts} após {backoff_seconds}s"
        )
        await self._capture_terminal_provider_artifacts(
            job.id, f"provider_capacity_{status}_attempt_{job.attempts}"
        )
        self.store.return_to_pending(job.id, "generating", reason)
        event(
            self.logger,
            logging.WARNING,
            "provider_capacity_retry_scheduled",
            job_id=job.id,
            attempt=job.attempts,
            next_attempt=job.attempts + 1,
            status=status,
            state=state,
            backoff_seconds=backoff_seconds,
            url=failure.get("url") if failure is not None else None,
            body=failure.get("body") if failure is not None else "Firefly UI capacity overlay",
        )
        await asyncio.sleep(backoff_seconds)
        return True

    async def _provider_capacity_overlay_visible(self, page: object) -> bool:
        """Detecta o limite temporário exibido pelo Firefly sem depender de seletor."""
        try:
            return bool(
                await page.evaluate(
                    r"""() => {
                        const seen = new Set();
                        const markers = [
                            'estamos ansiosos para gerar mais conteúdo',
                            'we are excited to generate more content',
                            'calma aí, piloto',
                            'slow down'
                        ];
                        const visible = (node) => {
                            if (!node?.getBoundingClientRect) return false;
                            const rect = node.getBoundingClientRect();
                            const style = getComputedStyle(node);
                            return rect.width > 0 && rect.height > 0 &&
                                style.display !== 'none' && style.visibility !== 'hidden';
                        };
                        const walk = (root) => {
                            if (!root || seen.has(root)) return false;
                            seen.add(root);
                            const nodes = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                            for (const node of nodes) {
                                const text = (node.innerText || node.textContent || '')
                                    .replace(/\s+/g, ' ').trim().toLowerCase();
                                if (visible(node) && markers.some((marker) => text.includes(marker))) return true;
                                if (node.shadowRoot && walk(node.shadowRoot)) return true;
                            }
                            return false;
                        };
                        return walk(document);
                    }"""
                )
            )
        except (PatchrightTimeoutError, PatchrightError):
            return False

    async def _click_generate_and_confirm(
        self, job: Job, page: object, human_input: HumanInput
    ) -> None:
        generate_button = locator_for(page, "generate_button")
        generate_button_host = locator_for(page, "generate_button_host")
        state_reader = StateReader(page, self.config.screenshots_dir, self.logger, self.config)

        for attempt in range(1, 5):
            # O botão de geração é um web component e, em momentos de troca de
            # job, pode permanecer visualmente ativo sem receber o primeiro
            # evento. Revalidar a aba e repetir o gesto torna o disparo
            # observável sem aceitar um estado ``idle`` como geração iniciada.
            if attempt > 1:
                try:
                    await dismiss_overlays(page, human_input, self.logger, job.id)
                    await page.wait_for_timeout(750)
                except PatchrightError as exc:
                    event(
                        self.logger,
                        logging.WARNING,
                        "generate_click_retry_page_not_ready",
                        job_id=job.id,
                        attempt=attempt,
                        error=str(exc),
                    )
                    continue

            if attempt == 1:
                await generate_button.click(
                    timeout=self.config.ui_response_timeout_ms, force=True
                )
            elif attempt == 2:
                event(
                    self.logger,
                    logging.WARNING,
                    "generate_click_retry_host_coordinates",
                    job_id=job.id,
                    attempt=attempt,
                )
                box = await generate_button_host.bounding_box(
                    timeout=self.config.ui_response_timeout_ms
                )
                if box:
                    await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            elif attempt == 3:
                event(
                    self.logger,
                    logging.WARNING,
                    "generate_click_retry_shadow_dom",
                    job_id=job.id,
                    attempt=attempt,
                )
                clicked = await page.evaluate(
                    """() => {
                        const seen = new Set();
                        function walk(root) {
                            if (!root || seen.has(root)) return null;
                            seen.add(root);
                            const host = root.querySelector && root.querySelector('firefly-video-generation-generate-button');
                            if (host) return host;
                            const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                            for (const el of elements) {
                                const found = el.shadowRoot ? walk(el.shadowRoot) : null;
                                if (found) return found;
                            }
                            return null;
                        }
                        const host = walk(document);
                        if (!host) return false;
                        const inner = host.shadowRoot && host.shadowRoot.querySelector('[data-testid="video-generation-generate-button"], sp-button, button');
                        const target = inner || host;
                        target.removeAttribute && target.removeAttribute('aria-disabled');
                        target.removeAttribute && target.removeAttribute('disabled');
                        target.click();
                        return true;
                    }"""
                )
                if not clicked:
                    await generate_button.click(timeout=self.config.ui_response_timeout_ms, force=True)
            else:
                event(
                    self.logger,
                    logging.WARNING,
                    "generate_click_retry_test_id_dispatch",
                    job_id=job.id,
                    attempt=attempt,
                )
                dispatched = await page.evaluate(
                    """() => {
                        const button = document.querySelector('[data-testid=\"video-generation-generate-button\"]');
                        if (!button) return false;
                        button.dispatchEvent(new MouseEvent('pointerdown', {bubbles: true, composed: true}));
                        button.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, composed: true}));
                        button.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, composed: true}));
                        button.dispatchEvent(new MouseEvent('click', {bubbles: true, composed: true}));
                        return true;
                    }"""
                )
                if not dispatched:
                    await generate_button.click(
                        timeout=self.config.ui_response_timeout_ms, force=True
                    )

            deadline = asyncio.get_running_loop().time() + 18
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(1)
                observation = await state_reader.read_dom_state_fast(job.id)
                if observation.state in {
                    ScreenState.STILL_GENERATING,
                    ScreenState.RESULT_READY,
                    ScreenState.ERROR_TOAST,
                    ScreenState.CONTENT_REJECTED,
                    ScreenState.QUOTA_EXHAUSTED,
                }:
                    event(
                        self.logger,
                        logging.INFO,
                        "generate_click_confirmed",
                        job_id=job.id,
                        attempt=attempt,
                        state=observation.state.value,
                    )
                    return

        raise RuntimeError("generate button click did not start provider generation")

    async def _complete_generation(
        self, job: Job, page: object, state_reader: StateReader
    ) -> None:
        """Aguarda remotamente; volta ao primeiro plano apenas para exportar."""
        observation = await self._poll_generation(job, state_reader)
        if observation.state is ScreenState.RESULT_READY:
            provider_url = self._provider_result_urls.pop(job.id, None)
            provider_metadata = self._provider_result_metadata.pop(job.id, {})
            identity = (
                {
                    "provider": "Adobe Firefly",
                    "source": "provider_api_result",
                    "provider_video_id": provider_metadata.get("video_id"),
                    "provider_result_recovery_capability": "presigned_url",
                }
                if provider_url
                else await capture_provider_result_identity(page, job, observation)
            )
            identity_path = persist_provider_result_binding(
                Path(os.environ.get("FIREFLY_DIAG_DIR", str(self.config.screenshots_dir))),
                job=job,
                identity=identity,
                source_shot_id=(job.name or "").split("_TAKE_", maxsplit=1)[0] or None,
            )
            event(
                self.logger,
                logging.INFO,
                "provider_result_identity_persisted",
                job_id=job.id,
                path=str(identity_path),
                capability=identity.get("provider_result_recovery_capability"),
            )
            # A tela é lida antes da limpeza: modal terminal nunca é descartado sem diagnóstico.
            if provider_url:
                validated_download = await export_video_from_provider_url(
                    page,
                    provider_url,
                    self.config,
                    self.logger,
                    job.id,
                )
            else:
                async with self._foreground_page(page):
                    validated_download = await export_video(
                        page,
                        self._require_human(),
                        self.config,
                        self.logger,
                        job.id,
                    )
            published_path = self._publish_batch_output(validated_download.path, job)
            validation_time = time.time()
            self.store.transition(
                job.id,
                "generating",
                "done",
                output_path=str(published_path),
                media_metadata={
                    "download_started_at": validation_time,
                    "download_completed_at": validation_time,
                    "media_validated_at": validation_time,
                    "media_validation_status": "PASS",
                    "media_validation_error": None,
                    "file_size_bytes": validated_download.file_size_bytes,
                    "sha256": validated_download.sha256,
                    "width": validated_download.width,
                    "height": validated_download.height,
                    "codec": validated_download.codec,
                },
            )
            event(
                self.logger,
                logging.INFO,
                "job_done",
                job_id=job.id,
                output_path=published_path,
                file_size_bytes=validated_download.file_size_bytes,
                sha256=validated_download.sha256,
                width=validated_download.width,
                height=validated_download.height,
                duration_seconds=validated_download.duration_seconds,
                codec=validated_download.codec,
            )
            return
        if observation.state is ScreenState.CONTENT_REJECTED:
            artifacts = await self._capture_terminal_provider_artifacts(
                job.id, "content_rejected"
            )
            self.store.transition(
                job.id, "generating", "failed-content", error="conteúdo rejeitado"
            )
            event(
                self.logger,
                logging.WARNING,
                "content_rejected",
                job_id=job.id,
                screenshot=artifacts.get("screenshot_path"),
                html=artifacts.get("html_path"),
                visible_text=artifacts.get("visible_text_path"),
                provider_reason=artifacts.get("provider_reason_path"),
            )
            return
        if observation.state is ScreenState.ERROR_TOAST:
            if await self._retry_after_provider_capacity_error(
                job, observation.state.value
            ):
                return
            artifacts = await self._capture_terminal_provider_artifacts(
                job.id, "error_toast"
            )
            reason = "provider retornou error_toast após iniciar a geração"
            self.store.transition(job.id, "generating", "failed-infra", error=reason)
            event(
                self.logger,
                logging.ERROR,
                "provider_error_toast",
                job_id=job.id,
                model=job.model,
                screenshot=artifacts.get("screenshot_path"),
                html=artifacts.get("html_path"),
                visible_text=artifacts.get("visible_text_path"),
                provider_reason=artifacts.get("provider_reason_path"),
            )
            if os.environ.get("FIREFLY_CONTINUE_ON_PROVIDER_ERROR") == "true":
                return
            self.store.set_system_status("paused-blocked", reason)
            raise QueuePausedError(reason)
        if observation.state is ScreenState.QUOTA_EXHAUSTED:
            self.store.return_to_pending(job.id, "generating", "quota esgotada")
            self.store.set_system_status("paused-quota", "quota esgotada")
            raise QueuePausedError("quota esgotada; fila pausada")
        if observation.state is ScreenState.LOGGED_OUT:
            self.store.return_to_pending(job.id, "generating", "sessão expirada durante geração")
            self.store.set_system_status("paused-auth", "sessão expirada durante geração")
            raise QueuePausedError("sessão expirada durante geração")
        reason = f"estado terminal não concluído: {observation.state.value}"
        if await self._retry_after_provider_capacity_error(
            job, observation.state.value
        ):
            return
        artifacts = await self._capture_terminal_provider_artifacts(
            job.id, observation.state.value
        )
        self.store.transition(job.id, "generating", "failed-infra", error=reason)
        event(
            self.logger,
            logging.ERROR,
            "provider_terminal_state_unresolved",
            job_id=job.id,
            model=job.model,
            state=observation.state.value,
            screenshot=artifacts.get("screenshot_path"),
            provider_reason=artifacts.get("provider_reason_path"),
        )
        self.store.set_system_status("paused-blocked", reason)
        raise QueuePausedError(reason)

    def _get_locator(self, key: str) -> object:
        if self._page is None:
            raise RuntimeError("page ainda não foi associada ao worker")
        return locator_for(self._page, key)

    def _require_human(self) -> HumanInput:
        if self._human is None:
            raise RuntimeError("HumanInput ainda não foi associado ao worker")
        return self._human

    async def _open_video_generation(self) -> None:
        """Abre a área de vídeo e garante uma tela limpa sem resíduos do job anterior."""
        if self._page is None:
            raise RuntimeError("page ainda não foi associada ao worker")
        human = self._require_human()
        if "/generate/video" in self._page.url:
            await self._page.goto(self.config.firefly_url, wait_until="domcontentloaded")
            await asyncio.sleep(1.0)
        else:
            await human.click_element(
                self._get_locator("generate_video_tab"), "entrada Generate Video"
            )
            await asyncio.sleep(1.0)
        try:
            limpar = self._page.get_by_text("Limpar tudo")
            if await limpar.is_visible():
                await limpar.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass

    async def _configure_model(self, model: str) -> None:
        normalized = model.casefold()
        if normalized == "kling 2.5 turbo":
            await self._select_picker_option(
                picker_key="model_dropdown",
                trigger_key="model_dropdown_trigger",
                option_key="model_option_kling25_turbo",
                expected_values={str(GENERATION_PROFILE["model_value"])},
                description="modelo Kling 2.5 Turbo",
            )
            return
        if normalized == "kling 3.0".casefold():
            await self._select_picker_option(
                picker_key="model_dropdown",
                trigger_key="model_dropdown_trigger",
                option_key="model_option_kling3",
                expected_values={"kling:firefly:colligo:v3direct"},
                description="modelo Kling 3.0",
            )
            return
        option = MODEL_PICKER_OPTIONS.get(normalized)
        if option is None:
            raise ValueError(f"modelo ainda não suportado: {model}")
        expected_value, display_name = option
        picker = self._get_locator("model_dropdown")
        trigger = self._get_locator("model_dropdown_trigger")
        picker_value = (await picker.get_attribute("value") or "").casefold()
        trigger_text = (await trigger.inner_text()).casefold()
        if picker_value == expected_value.casefold() or display_name.casefold() in trigger_text:
            return
        await trigger.press("Enter")
        option_locator = self._page.get_by_test_id(f"firefly-menu-item-{expected_value}")
        await option_locator.wait_for(state="visible", timeout=self.config.selector_timeout_ms)
        await option_locator.click()
        deadline = asyncio.get_running_loop().time() + self.config.selector_timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            trigger_text = (await trigger.inner_text()).casefold()
            picker_value = (await picker.get_attribute("value") or "").casefold()
            if picker_value == expected_value.casefold() or display_name.casefold() in trigger_text:
                event(
                    self.logger, logging.INFO, "model_selected", job_id=self._current_job_id,
                    model=display_name, picker_value=picker_value,
                )
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(f"modelo {display_name} não foi confirmado após a seleção")

    async def _configure_resolution(self, resolution: str) -> None:
        normalized = resolution.casefold()
        if normalized == "720p":
            option_key = "resolution_option_720p"
            expected_values = {"720", "720p"}
            description = "resolução 720p"
        elif normalized == "1080p":
            option_key = "resolution_option_1080p"
            expected_values = {"1080", "1080p"}
            description = "resolução 1080p"
        else:
            raise ValueError(f"resolução ainda não suportada pelo fluxo v1: {resolution}")
        await self._select_picker_option(
            picker_key="resolution_dropdown",
            trigger_key="resolution_dropdown_trigger",
            option_key=option_key,
            expected_values=expected_values,
            description=description,
        )

    async def _configure_aspect_ratio(self, aspect: str) -> None:
        if aspect in {"16:9", "Widescreen (16:9)"}:
            option_key = "aspect_ratio_widescreen"
            expected_values = {'{"height":720,"width":1280}', '{"height":1080,"width":1920}', "16:9", "Widescreen (16:9)"}
            description = "aspect ratio Widescreen 16:9"
        elif aspect in {"9:16", "Vertical (9:16)"}:
            option_key = "aspect_ratio_vertical"
            expected_values = {'{"height":1280,"width":720}', '{"height":1920,"width":1080}', "9:16", "Vertical (9:16)"}
            description = "aspect ratio Vertical 9:16"
        else:
            raise ValueError(f"aspect ratio ainda não suportado pelo fluxo v1: {aspect}")
        await self._select_picker_option(
            picker_key="aspect_ratio_dropdown",
            trigger_key="aspect_ratio_dropdown_trigger",
            option_key=option_key,
            expected_values=expected_values,
            description=description,
        )

    async def _verify_frame_rate(self, fps: int) -> None:
        if self._page is None:
            raise RuntimeError("page ainda não foi associada ao worker")
        visible_text = await deep_composed_text(self._page)
        expected = f"{fps} FPS"
        if expected.casefold() not in visible_text.casefold():
            raise RuntimeError(f"FIREFLY_FRAME_RATE_NOT_CONFIRMED:{expected}")
        event(
            self.logger,
            logging.INFO,
            "frame_rate_verified_before_generate",
            job_id=self._current_job_id,
            fps=fps,
        )

    async def _configure_duration(self, seconds: int, model: str = "Kling 3.0") -> None:
        normalized_model = model.casefold()
        if normalized_model in FIXED_FIVE_SECOND_MODELS:
            if seconds != 5:
                raise ValueError(
                    f"{model} usa duração fixa de 5 segundos neste pipeline: {seconds}"
                )
            await self._write_duration_artifact(
                "duration_after.json",
                {
                    "job_id": self._current_job_id,
                    "model": model,
                    "requested_seconds": seconds,
                    "verified_seconds": 5,
                    "control_type": "provider_fixed_default",
                    "reason": f"{model} production contract is fixed at 5 seconds",
                },
            )
            event(
                self.logger,
                logging.INFO,
                "duration_verified_before_generate",
                job_id=self._current_job_id,
                requested_seconds=seconds,
                verified_seconds=5,
                control_type="provider_fixed_default",
            )
            return
        if normalized_model in MODEL_DEFAULT_DURATION_MODELS:
            await self._write_duration_artifact(
                "duration_after.json",
                {
                    "job_id": self._current_job_id,
                    "model": model,
                    "requested_seconds": seconds,
                    "verified_seconds": None,
                    "control_type": "model_default",
                    "reason": f"{model} did not expose the partner-model shot duration control",
                },
            )
            event(
                self.logger,
                logging.INFO,
                "duration_model_default_used",
                job_id=self._current_job_id,
                requested_seconds=seconds,
                model=model,
            )
            return
        is_veo = model.casefold() in {"veo 3.1 fast".casefold(), "veo 3.1".casefold()}
        if is_veo and seconds not in {4, 6, 8}:
            raise ValueError(f"duração do {model} precisa ser 4, 6 ou 8: {seconds}")
        if not is_veo and not 1 <= seconds <= 15:
            raise ValueError(f"duração do Kling 3.0 precisa estar entre 1 e 15: {seconds}")

        if is_veo:
            await self._configure_veo_duration(seconds, model)
            return

        if self._page is not None and hasattr(self._page, "evaluate"):
            controller = DurationController(self._page, self.config)
            await self._write_duration_artifact(
                "duration_before.json",
                {
                    "job_id": self._current_job_id,
                    "requested_seconds": seconds,
                    "signals": await controller.duration_value_signals(),
                },
            )
            try:
                result = await controller.configure(seconds)
                signals = await controller.duration_value_signals()
                await self._write_duration_artifact(
                    "duration_after.json",
                    {
                        "job_id": self._current_job_id,
                        "requested_seconds": seconds,
                        "verified_seconds": result.verified_value_seconds,
                        "capabilities": result.as_dict(),
                        "signals": signals,
                    },
                )
                event(
                    self.logger,
                    logging.INFO,
                    "duration_verified_before_generate",
                    job_id=self._current_job_id,
                    requested_seconds=seconds,
                    verified_seconds=result.verified_value_seconds,
                    control_type=result.observed_control,
                )
            except Exception as exc:
                await self._write_duration_artifact(
                    "duration_error.json",
                    {
                        "job_id": self._current_job_id,
                        "requested_seconds": seconds,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                raise
            return

        picker = self._get_locator("duration_dropdown")
        expected = {str(seconds), f"{seconds} segundos"}
        if await picker.get_attribute("value") in expected:
            return

        trigger = self._get_locator("duration_dropdown_trigger")
        await trigger.press("Enter")
        await self._require_human().human_delay(0.2, 0.5)

        # Algumas variantes do Firefly usam opções discretas; Kling 3.0 usa
        # slider. Preferimos a opção quando ela existe e caímos no slider.
        option = self._get_locator(f"duration_option_{seconds}")
        if await option.count() == 1 and await option.is_visible():
            await option.click()
            await self._wait_for_picker_value(picker, expected, "duração")
            return

        slider = self._get_locator("duration_slider")
        await slider.wait_for(state="visible", timeout=self.config.selector_timeout_ms)
        minimum = self._int_attribute(await slider.get_attribute("aria-valuemin"), 1)
        maximum = self._int_attribute(await slider.get_attribute("aria-valuemax"), 15)
        if not minimum <= seconds <= maximum:
            raise ValueError(
                f"duração {seconds}s fora do intervalo atual do Firefly: "
                f"{minimum}-{maximum}s"
            )
        await slider.press("Home")
        for _ in range(seconds - minimum):
            await slider.press("ArrowRight")
        await self._wait_for_duration_value(picker, slider, seconds)
        await slider.press("Escape")

    async def _configure_veo_duration(self, seconds: int, model: str) -> None:
        """Configure the discrete 4/6/8-second picker exposed by Veo models."""
        picker = self._get_locator("duration_dropdown")
        expected = {str(seconds), f"{seconds} segundos", f"{seconds} seconds"}
        if await picker.get_attribute("value") in expected:
            return
        trigger = self._get_locator("duration_dropdown_trigger")
        await trigger.press("Enter")
        option = self._get_locator(f"duration_option_{seconds}")
        await option.wait_for(state="visible", timeout=self.config.selector_timeout_ms)
        await option.click()
        await self._wait_for_picker_value(picker, expected, f"duração {model}")
        await self._write_duration_artifact(
            "duration_after.json",
            {
                "job_id": self._current_job_id,
                "model": model,
                "requested_seconds": seconds,
                "verified_seconds": seconds,
                "control_type": "discrete_picker",
                "picker_value": await picker.get_attribute("value"),
            },
        )
        event(
            self.logger,
            logging.INFO,
            "duration_verified_before_generate",
            job_id=self._current_job_id,
            requested_seconds=seconds,
            verified_seconds=seconds,
            control_type="discrete_picker",
        )

    async def _select_picker_option(
        self,
        *,
        picker_key: str,
        trigger_key: str,
        option_key: str,
        expected_values: set[str],
        description: str,
    ) -> None:
        """Seleciona e confirma um sp-picker; nunca avança após click ineficaz."""
        picker = self._get_locator(picker_key)
        if await picker.get_attribute("value") in expected_values:
            return
        trigger = self._get_locator(trigger_key)
        # Click no host <sp-picker> não abre o menu na UI atual. O botão
        # interno responde de forma confiável ao teclado.
        await trigger.press("Enter")
        option = self._get_locator(option_key)
        await option.wait_for(state="visible", timeout=self.config.selector_timeout_ms)
        await option.click()
        await self._wait_for_picker_value(picker, expected_values, description)
        await self._require_human().human_delay(0.2, 0.5)

    async def _wait_for_picker_value(
        self, picker: object, expected_values: set[str], description: str
    ) -> None:
        deadline = (
            asyncio.get_running_loop().time() + self.config.selector_timeout_ms / 1000
        )
        current: str | None = None
        while asyncio.get_running_loop().time() < deadline:
            current = await picker.get_attribute("value")
            if current in expected_values:
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(
            f"{description} não foi aplicado; valor atual={current!r}, "
            f"esperado={sorted(expected_values)!r}"
        )

    async def _wait_for_duration_value(
        self, picker: object, slider: object, seconds: int
    ) -> None:
        deadline = (
            asyncio.get_running_loop().time() + self.config.selector_timeout_ms / 1000
        )
        expected = str(seconds)
        picker_value: str | None = None
        slider_value: str | None = None
        while asyncio.get_running_loop().time() < deadline:
            picker_value = await picker.get_attribute("value")
            slider_value = (
                await slider.get_attribute("aria-valuenow")
                or await slider.get_attribute("value")
            )
            if picker_value in {expected, f"{seconds} segundos"} or slider_value == expected:
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(
            f"duração não foi aplicada; picker={picker_value!r}, "
            f"slider={slider_value!r}, esperado={seconds}s"
        )

    @staticmethod
    def _int_attribute(value: str | None, default: int) -> int:
        try:
            return int(value) if value is not None else default
        except ValueError:
            return default

    async def _write_duration_artifact(self, name: str, payload: object) -> None:
        base = os.environ.get("FIREFLY_DIAG_DIR")
        if not base:
            return
        duration_dir = Path(base) / "duration"
        duration_dir.mkdir(parents=True, exist_ok=True)
        (duration_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def _configure_audio(self, enabled: bool) -> None:
        definition = ACTION_SELECTORS["audio_toggle"]
        if not definition.confirmed:
            event(
                self.logger,
                logging.INFO,
                "audio_toggle_skipped",
                job_id=self._current_job_id,
                reason="selector_unconfirmed",
            )
            if enabled:
                raise UnconfirmedSelectorError("audio_toggle")
            return
        toggle = self._get_locator("audio_toggle")
        if not enabled:
            try:
                count = await toggle.count()
                visible = count > 0 and await toggle.first.is_visible()
            except Exception:
                visible = False
            if not visible:
                event(
                    self.logger, logging.INFO, "audio_toggle_absent_skipped",
                    job_id=self._current_job_id, enabled=enabled,
                )
                return
        current = await toggle.get_attribute("aria-checked") == "true"
        if current != enabled:
            await self._require_human().click_element(
                toggle, "ativar áudio" if enabled else "desativar áudio"
            )
        deadline = asyncio.get_running_loop().time() + self.config.selector_timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            current = await toggle.get_attribute("aria-checked") == "true"
            if current == enabled:
                event(
                    self.logger, logging.INFO, "audio_configured",
                    job_id=self._current_job_id, enabled=enabled,
                )
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(f"estado do áudio não foi confirmado: enabled={enabled}")

    async def _disable_audio(self) -> None:
        """Compatibilidade com testes e chamadas antigas."""
        await self._configure_audio(False)

    async def _upload_first_frame(self, image_path: str) -> None:
        path = Path(image_path).resolve()
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise FileNotFoundError(f"first frame inválido: {path}")
        human = self._require_human()
        editor = self._get_locator("first_frame_editor")
        has_file_chooser = self._page is not None and hasattr(self._page, "expect_file_chooser")
        first_frame_button = (
            editor.get_by_test_id("placeholder-upload-button").nth(0)
            if has_file_chooser
            else self._get_locator("first_frame_button")
        )
        async def editor_snapshot(stage: str) -> None:
            try:
                snapshot = await editor.evaluate(
                    """(host) => {
                        const rows = [];
                        const walk = (root, depth = 0) => {
                            for (const el of root.querySelectorAll('*')) {
                                const tag = el.tagName.toLowerCase();
                                if (['input', 'button', 'sp-button', 'img', 'canvas', 'firefly-upload-new'].includes(tag)) {
                                    rows.push({
                                        tag,
                                        testId: el.getAttribute('data-testid'),
                                        id: el.id || null,
                                        type: el.getAttribute('type'),
                                        accept: el.getAttribute('accept'),
                                        text: (el.textContent || '').trim().slice(0, 120),
                                        src: (el.getAttribute('src') || '').slice(0, 160),
                                        hidden: !!el.hidden,
                                        disabled: !!el.disabled,
                                        depth
                                    });
                                }
                                if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
                            }
                        };
                        walk(host.shadowRoot || host);
                        return rows;
                    }"""
                )
                event(
                    self.logger,
                    logging.INFO,
                    "first_frame_editor_snapshot",
                    job_id=self._current_job_id,
                    stage=stage,
                    controls=snapshot,
                )
            except Exception as exc:
                event(
                    self.logger,
                    logging.WARNING,
                    "first_frame_editor_snapshot_failed",
                    job_id=self._current_job_id,
                    stage=stage,
                    error=str(exc),
                )

        # Os caches pertencem ao runtime autenticado do Firefly. Apaga-los com a
        # pagina aberta invalida o pipeline de assets e pode consumir o FileList
        # sem iniciar nenhuma requisicao de upload.
        event(
            self.logger,
            logging.INFO,
            "first_frame_asset_cache_preserved",
            job_id=self._current_job_id,
        )

        await editor_snapshot("before_upload")
        thumbnail = self._get_locator("first_frame_thumbnail")

        async def uploaded_control_visible(timeout_ms: int) -> bool:
            """Confirm the accepted frame inside Firefly's nested shadow roots.

            Patchright's page-level test-id locator can lag behind the web
            component even though its Remove/Replace controls are already
            rendered.  Those controls only exist after Firefly has accepted
            the frame, so they are a safe positive confirmation.
            """
            deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
            while asyncio.get_running_loop().time() < deadline:
                remaining_ms = max(
                    1,
                    int((deadline - asyncio.get_running_loop().time()) * 1000),
                )
                try:
                    await thumbnail.wait_for(
                        state="visible", timeout=min(750, remaining_ms)
                    )
                    return True
                except PatchrightTimeoutError:
                    pass
                try:
                    confirmed = await editor.evaluate(
                        """(host) => {
                            const visible = (element) => {
                                const style = getComputedStyle(element);
                                const rect = element.getBoundingClientRect();
                                return !element.hidden && style.display !== 'none' &&
                                    style.visibility !== 'hidden' &&
                                    (rect.width > 0 || rect.height > 0);
                            };
                            const accepted = (element) => {
                                const testId = element.getAttribute('data-testid') || '';
                                const label = [
                                    element.getAttribute('aria-label') || '',
                                    element.getAttribute('title') || '',
                                    element.textContent || ''
                                ].join(' ').trim().toLocaleLowerCase();
                                return testId === 'remove-frame-0' ||
                                    label.includes('remover imagem') ||
                                    label.includes('remove image') ||
                                    label.includes('substituir imagem') ||
                                    label.includes('replace image');
                            };
                            const walk = (root) => {
                                for (const element of root.querySelectorAll('*')) {
                                    if (accepted(element) && visible(element)) return true;
                                    if (element.shadowRoot && walk(element.shadowRoot)) return true;
                                }
                                return false;
                            };
                            return walk(host.shadowRoot || host);
                        }"""
                    )
                    if confirmed:
                        return True
                except Exception:
                    # Some test doubles and older Firefly components do not
                    # expose evaluate here; the confirmed selector remains the
                    # primary path in that case.
                    pass
                await asyncio.sleep(0.2)
            return False

        if has_file_chooser:
            scoped_input = editor.locator('input[type="file"][accept*="image"]').nth(0)
            await scoped_input.set_input_files(str(path))
            await human.human_delay(1.0, 1.5)
            await editor_snapshot("after_scoped_input")
            if await uploaded_control_visible(self.config.selector_timeout_ms):
                event(
                    self.logger,
                    logging.INFO,
                    "first_frame_uploaded",
                    job_id=self._current_job_id,
                    image=path,
                    method="scoped_input_confirmed_control",
                )
                return
            event(
                self.logger,
                logging.WARNING,
                "first_frame_scoped_input_not_confirmed",
                job_id=self._current_job_id,
            )

        if has_file_chooser:
            # A UI atual do Firefly abre o seletor nativo diretamente ao clicar
            # no placeholder "Primeiro". Versões anteriores exibiam antes um
            # menu intermediário "Seu dispositivo". Capturamos primeiro o
            # fluxo direto para não deixar o diálogo nativo aberto sem handler.
            direct_chooser = None
            try:
                async with self._page.expect_file_chooser(
                    timeout=self.config.selector_timeout_ms
                ) as direct_chooser_info:
                    await human.click_element(
                        first_frame_button, "botão First frame"
                    )
                direct_chooser = await direct_chooser_info.value
            except PatchrightTimeoutError:
                event(
                    self.logger,
                    logging.INFO,
                    "first_frame_direct_file_chooser_not_observed",
                    job_id=self._current_job_id,
                )

            if direct_chooser is not None:
                chooser_element = direct_chooser.element
                accept = await chooser_element.get_attribute("accept")
                if accept and "image" not in accept.casefold():
                    raise RuntimeError(
                        f"FIREFLY_WRONG_FILE_CHOOSER: expected image input, accept={accept}"
                    )
                await direct_chooser.set_files(str(path))
                await human.human_delay(1.0, 1.5)
                await editor_snapshot("after_direct_file_chooser")
                if await uploaded_control_visible(self.config.selector_timeout_ms):
                    event(
                        self.logger,
                        logging.INFO,
                        "first_frame_uploaded",
                        job_id=self._current_job_id,
                        image=path,
                        method="direct_file_chooser",
                    )
                    return
                event(
                    self.logger,
                    logging.WARNING,
                    "first_frame_direct_file_chooser_not_confirmed",
                    job_id=self._current_job_id,
                )
            else:
                await human.human_delay(0.3, 0.8)

            local_file = None
            deadline = asyncio.get_running_loop().time() + self.config.selector_timeout_ms / 1000
            while local_file is None and asyncio.get_running_loop().time() < deadline:
                if await uploaded_control_visible(500):
                    event(
                        self.logger,
                        logging.INFO,
                        "first_frame_uploaded",
                        job_id=self._current_job_id,
                        image=path,
                        method="late_scoped_input_confirmed_control",
                    )
                    return
                for label in ("Seu dispositivo", "Your device"):
                    # O menu pode ser renderizado em um portal fora do shadow
                    # root do editor; procure primeiro na página inteira.
                    candidates = self._page.get_by_text(label, exact=True)
                    for index in range(await candidates.count()):
                        candidate = candidates.nth(index)
                        if await candidate.is_visible(timeout=250):
                            local_file = candidate
                            break
                    if local_file is not None:
                        break
                if local_file is None:
                    await asyncio.sleep(0.1)
            if local_file is None:
                if await uploaded_control_visible(1500):
                    event(
                        self.logger,
                        logging.INFO,
                        "first_frame_uploaded",
                        job_id=self._current_job_id,
                        image=path,
                        method="late_scoped_input_confirmed_control",
                    )
                    return
                raise RuntimeError("FIREFLY_LOCAL_FILE_MENU_NOT_VISIBLE")
            async with self._page.expect_file_chooser(
                timeout=self.config.selector_timeout_ms
            ) as chooser_info:
                await local_file.click()
            chooser = await chooser_info.value
            chooser_element = chooser.element
            accept = await chooser_element.get_attribute("accept")
            if accept and "image" not in accept.casefold():
                raise RuntimeError(
                    f"FIREFLY_WRONG_FILE_CHOOSER: expected image input, accept={accept}"
                )
            event(
                self.logger,
                logging.INFO,
                "first_frame_file_chooser_confirmed",
                job_id=self._current_job_id,
                accept=accept,
            )
            # FileChooser.set_files traverses the browser's native upload path.
            # Setting the ElementHandle directly left C:\\fakepath populated but
            # did not update the keyframe editor's application state.
            await chooser.set_files(str(path))
            await human.human_delay(1.0, 1.5)
            await editor_snapshot("after_file_chooser")
            file_state = await chooser_element.evaluate(
                """(input) => ({
                    value: input.value || null,
                    files: Array.from(input.files || []).map((file) => ({
                        name: file.name, size: file.size, type: file.type
                    }))
                })"""
            )
            event(
                self.logger,
                logging.INFO,
                "first_frame_input_populated",
                job_id=self._current_job_id,
                file_state=file_state,
            )
            # The editor replaces its input after accepting a file, so an empty
            # replacement input is not a failure. The rendered thumbnail below
            # remains the authoritative positive confirmation.
        else:
            await human.click_element(
                first_frame_button, "botão First frame"
            )
            await human.human_delay(0.3, 0.8)
            await self._get_locator("first_frame_upload").set_input_files(str(path))
            try:
                await thumbnail.wait_for(state="visible", timeout=5000)
                event(
                    self.logger,
                    logging.INFO,
                    "first_frame_uploaded",
                    job_id=self._current_job_id,
                    image=path,
                )
                return
            except PatchrightTimeoutError:
                pass
        if not await uploaded_control_visible(5000):
            mime_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            # The current Firefly web component owns drag/drop on the
            # firefly-upload-new host, not on its visual placeholder button.
            dropzone = editor.locator("firefly-upload-new").nth(0)
            await dropzone.evaluate(
                """(target, payload) => {
                    const binary = atob(payload.base64);
                    const bytes = new Uint8Array(binary.length);
                    for (let index = 0; index < binary.length; index++) {
                        bytes[index] = binary.charCodeAt(index);
                    }
                    const transfer = new DataTransfer();
                    transfer.items.add(new File([bytes], payload.name, {type: payload.type}));
                    for (const type of ['dragenter', 'dragover', 'drop']) {
                        target.dispatchEvent(new DragEvent(type, {
                            bubbles: true, composed: true, cancelable: true,
                            dataTransfer: transfer
                        }));
                    }
                }""",
                {"base64": encoded, "name": path.name, "type": mime_type},
            )
            event(
                self.logger,
                logging.INFO,
                "first_frame_drop_fallback_dispatched",
                job_id=self._current_job_id,
                image=path,
            )
            if not await uploaded_control_visible(5000):
                await self._upload_first_frame_with_native_dialog(path, editor)
                if not await uploaded_control_visible(self.config.selector_timeout_ms):
                    raise RuntimeError("FIREFLY_FIRST_FRAME_NOT_CONFIRMED")
        event(
            self.logger,
            logging.INFO,
            "first_frame_uploaded",
            job_id=self._current_job_id,
            image=path,
        )

    async def _upload_first_frame_with_native_dialog(
        self, path: Path, editor: object
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("FIREFLY_NATIVE_FILE_CHOOSER_REQUIRES_WINDOWS")
        helper = resolve_native_file_chooser_helper(self.config.root_dir)

        await self._require_human().click_element(
            editor.get_by_test_id("placeholder-upload-button").nth(0), "botão First frame"
        )
        await self._require_human().human_delay(0.3, 0.8)
        local_file = None
        for label in ("Seu dispositivo", "Your device"):
            candidates = editor.get_by_text(label, exact=True)
            for index in range(await candidates.count()):
                candidate = candidates.nth(index)
                if await candidate.is_visible(timeout=500):
                    local_file = candidate
                    break
            if local_file is not None:
                break
        if local_file is None:
            raise RuntimeError("FIREFLY_NATIVE_LOCAL_FILE_MENU_NOT_VISIBLE")

        page = self._page
        if page is None:
            raise RuntimeError("FIREFLY_PAGE_NOT_AVAILABLE_FOR_FILE_CHOOSER")
        try:
            async with page.expect_file_chooser(timeout=10000) as chooser_info:
                await local_file.click(no_wait_after=True)
            chooser = await chooser_info.value
            await chooser.set_files(str(path))
            event(
                self.logger,
                logging.INFO,
                "first_frame_local_menu_file_selected",
                job_id=self._current_job_id,
                image=path,
            )
            return
        except PatchrightTimeoutError:
            event(
                self.logger,
                logging.WARNING,
                "first_frame_local_menu_file_chooser_not_observed",
                job_id=self._current_job_id,
            )

        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-FilePath",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await local_file.click(no_wait_after=True)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError("FIREFLY_NATIVE_FILE_CHOOSER_TIMEOUT")
        if process.returncode != 0:
            raise RuntimeError(
                "FIREFLY_NATIVE_FILE_CHOOSER_FAILED: "
                f"exit={process.returncode} stdout={stdout.decode(errors='replace')} "
                f"stderr={stderr.decode(errors='replace')}"
            )
        event(
            self.logger,
            logging.INFO,
            "first_frame_native_file_selected",
            job_id=self._current_job_id,
            image=path,
        )

    def _publish_batch_output(self, output_path: Path, job: Job) -> Path:
        if not job.name:
            return output_path
        output_dir = self.config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = (output_dir / f"{job.name}{output_path.suffix.lower()}").resolve()
        if not destination.is_relative_to(output_dir.resolve()):
            raise ValueError(f"nome de saída sai da pasta permitida: {job.name!r}")
        if destination.exists():
            raise FileExistsError(f"saída já existe; não será sobrescrita: {destination}")
        return output_path.replace(destination)

    async def _poll_generation(self, job: Job, state_reader: StateReader) -> ScreenObservation:
        started = asyncio.get_running_loop().time()
        consecutive_unknown = 0
        while True:
            if job.id in self._provider_result_urls:
                event(
                    self.logger,
                    logging.INFO,
                    "provider_api_result_ready",
                    job_id=job.id,
                )
                return ScreenObservation(
                    ScreenState.RESULT_READY,
                    url=str(getattr(state_reader.page, "url", self.config.firefly_url)),
                    selectors_found=("provider_api:result_ready",),
                )
            if hasattr(state_reader, "page") and self._human is not None:
                await dismiss_overlays(state_reader.page, self._human, self.logger, job.id)
                if await self._provider_capacity_overlay_visible(state_reader.page):
                    self._provider_capacity_overlay_seen = True
                    event(
                        self.logger,
                        logging.WARNING,
                        "provider_capacity_overlay_observed",
                        job_id=job.id,
                        state="slow_down",
                    )
                    return ScreenObservation(
                        ScreenState.ERROR_TOAST,
                        url=str(getattr(state_reader.page, "url", self.config.firefly_url)),
                        selectors_found=("provider_ui:slow_down",),
                    )
            observation = await self._read_generation_state(job, state_reader)
            capacity_failure = self._latest_retryable_provider_failure()
            if capacity_failure is not None:
                status = int(capacity_failure.get("status") or 0)
                event(
                    self.logger,
                    logging.WARNING,
                    "provider_capacity_failure_observed",
                    job_id=job.id,
                    status=status,
                    url=capacity_failure.get("url"),
                    body=capacity_failure.get("body"),
                )
                return ScreenObservation(
                    ScreenState.ERROR_TOAST,
                    url=observation.url,
                    selectors_found=(f"provider_network:http_{status}",),
                )
            if observation.state is ScreenState.UNKNOWN:
                consecutive_unknown += 1
                elapsed = asyncio.get_running_loop().time() - started
                event(
                    self.logger,
                    logging.WARNING,
                    "generation_state_transient",
                    job_id=job.id,
                    state=observation.state.value,
                    consecutive=consecutive_unknown,
                    elapsed_seconds=round(elapsed, 1),
                )
                # O Firefly pode remover o texto de progresso enquanto move o
                # resultado para o histórico. Três leituras (~9 s) eram curtas
                # demais e transformavam uma transição normal em falha.
                if consecutive_unknown >= max(self.config.UNKNOWN_THRESHOLD, 20):
                    return observation
                if elapsed >= self.config.generation_budget_seconds:
                    raise PatchrightTimeoutError("orçamento de geração excedido")
                await asyncio.sleep(self.config.poll_interval_seconds)
                continue
            if observation.state is not ScreenState.STILL_GENERATING:
                return observation
            consecutive_unknown = 0
            elapsed = asyncio.get_running_loop().time() - started
            event(
                self.logger,
                logging.INFO,
                "generation_poll",
                job_id=job.id,
                state=observation.state.value,
                elapsed_seconds=round(elapsed, 1),
            )
            if elapsed >= self.config.generation_budget_seconds:
                raise PatchrightTimeoutError("orçamento de geração excedido")
            # Polling explícito de estado, não uma espera cega por UI.
            await asyncio.sleep(self.config.poll_interval_seconds)

    async def _read_generation_state(
        self, job: Job, state_reader: StateReader
    ) -> ScreenObservation:
        try:
            observation = await asyncio.wait_for(
                state_reader.read_screen_state(job.id),
                timeout=self.config.state_read_timeout_seconds,
            )
            if observation.state is ScreenState.UNKNOWN and hasattr(
                state_reader, "read_dom_state_fast"
            ):
                dom_observation = await asyncio.wait_for(
                    state_reader.read_dom_state_fast(job.id),
                    timeout=5,
                )
                if dom_observation.state is not ScreenState.UNKNOWN:
                    return dom_observation
            return observation
        except TimeoutError:
            event(
                self.logger,
                logging.WARNING,
                "screen_state_read_timeout",
                slot_id=self._slot_id,
                job_id=job.id,
            )
        # A UI do Firefly pode congelar timers de uma aba não visível apesar das
        # flags do Chromium. Uma única releitura em primeiro plano recupera o slot.
        try:
            async with self._foreground_page(state_reader.page):
                return await asyncio.wait_for(
                    state_reader.read_screen_state(job.id),
                    timeout=self.config.state_read_timeout_seconds,
                )
        except TimeoutError:
            try:
                return await asyncio.wait_for(
                    state_reader.read_dom_state_fast(job.id),
                    timeout=5,
                )
            except TimeoutError:
                pass
            event(
                self.logger,
                logging.WARNING,
                "state_reader_recovery_timeout",
                slot_id=self._slot_id,
                job_id=job.id,
                state="unknown",
                timeout_seconds=self.config.state_read_timeout_seconds,
            )
            return ScreenObservation(ScreenState.UNKNOWN, url=getattr(state_reader.page, "url", None))

    async def _fail_infrastructure(self, job: Job, exc: Exception) -> None:
        traceback_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        artifacts = await self._capture_failure_artifacts(job.id, traceback_text)
        screenshot = artifacts.get("screenshot_path")
        try:
            error_text = f"{type(exc).__name__}: {exc}"
            media_metadata = None
            if "FAILED_MEDIA_VALIDATION" in error_text:
                media_metadata = {
                    "media_validated_at": time.time(),
                    "media_validation_status": "FAILED_MEDIA_VALIDATION",
                    "media_validation_error": error_text,
                }
            self.store.transition(
                job.id,
                "claimed",
                "failed-infra",
                error=error_text,
                media_metadata=media_metadata,
            )
        except ConcurrentTransitionError:
            try:
                self.store.transition(
                    job.id,
                    "generating",
                    "failed-infra",
                    error=error_text,
                    media_metadata=media_metadata,
                )
            except ConcurrentTransitionError:
                event(
                    self.logger,
                    logging.ERROR,
                    "failure_transition_skipped",
                    job_id=job.id,
                    error="estado já foi alterado por outro processo",
                )
        event(
            self.logger,
            logging.ERROR,
            "job_failed_infra",
            job_id=job.id,
            error=f"{type(exc).__name__}: {exc}",
            screenshot=screenshot,
            traceback_path=artifacts.get("traceback_path"),
            failure_url=artifacts.get("url"),
        )

    async def _capture_failure_artifacts(self, job_id: int, traceback_text: str) -> dict[str, str | None]:
        base_dir = Path(os.environ.get("FIREFLY_DIAG_DIR", str(self.config.screenshots_dir)))
        artifact_dir = base_dir / "browser_failure_artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        traceback_path = artifact_dir / f"job_{job_id}_traceback.txt"
        traceback_path.write_text(traceback_text, encoding="utf-8")
        artifacts: dict[str, str | None] = {
            "traceback_path": str(traceback_path),
            "screenshot_path": None,
            "html_path": None,
            "url_path": None,
            "dom_summary_path": None,
            "url": None,
        }
        if self._page is None:
            return artifacts

        url = getattr(self._page, "url", "")
        artifacts["url"] = url
        url_path = artifact_dir / f"job_{job_id}_failure_url.txt"
        url_path.write_text(url, encoding="utf-8")
        artifacts["url_path"] = str(url_path)

        screenshot_path = artifact_dir / f"job_{job_id}_failure_screenshot.png"
        try:
            await self._page.screenshot(path=str(screenshot_path), full_page=True)
            artifacts["screenshot_path"] = str(screenshot_path)
        except (PatchrightTimeoutError, PatchrightError) as exc:
            event(
                self.logger,
                logging.ERROR,
                "failure_screenshot_failed",
                job_id=job_id,
                error=type(exc).__name__,
            )

        html_path = artifact_dir / f"job_{job_id}_failure_page.html"
        try:
            html = await self._page.content()
            html_path.write_text(html, encoding="utf-8", errors="replace")
            artifacts["html_path"] = str(html_path)
        except (PatchrightTimeoutError, PatchrightError) as exc:
            html_path.write_text(f"PAGE_CONTENT_CAPTURE_FAILED: {type(exc).__name__}", encoding="utf-8")
            artifacts["html_path"] = str(html_path)

        dom_summary_path = artifact_dir / f"job_{job_id}_failure_dom_summary.json"
        try:
            summary = await self._page.evaluate(
                """() => {
                    const seen = new Set();
                    const texts = [];
                    const buttons = [];
                    let videos = 0;
                    let iframes = 0;
                    function isVisible(el) {
                        if (!el || !el.getBoundingClientRect) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return (rect.width > 0 || rect.height > 0) && style.visibility !== 'hidden' && style.display !== 'none';
                    }
                    function walk(root, path) {
                        if (!root || seen.has(root)) return;
                        seen.add(root);
                        const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                        for (const el of elements) {
                            if (el.tagName === 'VIDEO') videos += 1;
                            if (el.tagName === 'IFRAME') iframes += 1;
                            const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                            const dataTestId = el.getAttribute && el.getAttribute('data-testid');
                            const role = el.getAttribute && el.getAttribute('role');
                            if (isVisible(el) && text) texts.push({path, tag: el.tagName, text: text.slice(0, 300)});
                            if (role === 'button' || el.tagName === 'BUTTON' || el.tagName === 'SP-BUTTON' || dataTestId) {
                                buttons.push({
                                    path,
                                    tag: el.tagName,
                                    text: text.slice(0, 200),
                                    ariaLabel: el.getAttribute && el.getAttribute('aria-label'),
                                    dataTestId,
                                    disabled: el.hasAttribute && el.hasAttribute('disabled'),
                                    ariaDisabled: el.getAttribute && el.getAttribute('aria-disabled'),
                                    visible: isVisible(el)
                                });
                            }
                            if (el.shadowRoot) walk(el.shadowRoot, `${path}>${el.tagName.toLowerCase()}#shadow`);
                        }
                    }
                    walk(document, 'document');
                    return {
                        title: document.title,
                        url: location.href,
                        bodyText: texts.map(item => item.text).join('\\n').slice(0, 8000),
                        videos,
                        iframes,
                        buttons: buttons.slice(-120)
                    };
                }"""
            )
            dom_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            artifacts["dom_summary_path"] = str(dom_summary_path)
        except (PatchrightTimeoutError, PatchrightError) as exc:
            dom_summary_path.write_text(
                json.dumps({"error": type(exc).__name__}, indent=2),
                encoding="utf-8",
            )
            artifacts["dom_summary_path"] = str(dom_summary_path)
        return artifacts

    async def _capture_terminal_provider_artifacts(
        self, job_id: int, state: str
    ) -> dict[str, str | None]:
        base_dir = Path(os.environ.get("FIREFLY_DIAG_DIR", str(self.config.screenshots_dir)))
        artifact_dir = base_dir / "provider" / "terminal_state_artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts: dict[str, str | None] = {
            "screenshot_path": None,
            "html_path": None,
            "visible_text_path": None,
            "provider_reason_path": None,
            "url_path": None,
            "url": None,
        }
        if self._page is None:
            return artifacts

        url = getattr(self._page, "url", "")
        artifacts["url"] = url
        url_path = artifact_dir / f"job_{job_id}_{state}_url.txt"
        url_path.write_text(url, encoding="utf-8")
        artifacts["url_path"] = str(url_path)

        screenshot_path = artifact_dir / f"job_{job_id}_{state}_screenshot.png"
        try:
            await self._page.screenshot(path=str(screenshot_path), full_page=True)
            artifacts["screenshot_path"] = str(screenshot_path)
        except (PatchrightTimeoutError, PatchrightError) as exc:
            event(
                self.logger,
                logging.ERROR,
                "terminal_screenshot_failed",
                job_id=job_id,
                state=state,
                error=type(exc).__name__,
            )

        html_path = artifact_dir / f"job_{job_id}_{state}_page.html"
        try:
            html = await self._page.content()
            html_path.write_text(html, encoding="utf-8", errors="replace")
            artifacts["html_path"] = str(html_path)
        except (PatchrightTimeoutError, PatchrightError) as exc:
            html_path.write_text(
                f"PAGE_CONTENT_CAPTURE_FAILED: {type(exc).__name__}",
                encoding="utf-8",
            )
            artifacts["html_path"] = str(html_path)

        visible_text_path = artifact_dir / f"job_{job_id}_{state}_visible_text.txt"
        provider_reason_path = artifact_dir / f"job_{job_id}_{state}_provider_reason.json"
        try:
            payload = await self._page.evaluate(
                """() => {
                    const seen = new Set();
                    const visibleLines = [];
                    const dialogs = [];
                    function isVisible(el) {
                        if (!el || !el.getBoundingClientRect) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return (rect.width > 0 || rect.height > 0) &&
                            style.visibility !== 'hidden' && style.display !== 'none';
                    }
                    function walk(root, path) {
                        if (!root || seen.has(root)) return;
                        seen.add(root);
                        const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                        for (const el of elements) {
                            const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                            if (text && isVisible(el)) visibleLines.push(text);
                            const role = el.getAttribute && el.getAttribute('role');
                            const testId = el.getAttribute && el.getAttribute('data-testid');
                            const className = el.getAttribute && el.getAttribute('class');
                            if (
                                role === 'dialog' ||
                                /toast|dialog|error/i.test(String(testId || '')) ||
                                /toast|dialog|error/i.test(String(className || '')) ||
                                /ocorreu um erro|tente novamente|n[aã]o foi poss[ií]vel/i.test(text)
                            ) {
                                dialogs.push({path, tag: el.tagName, text: text.slice(0, 1000), role, testId, className});
                            }
                            if (el.shadowRoot) walk(el.shadowRoot, `${path}>${el.tagName.toLowerCase()}#shadow`);
                        }
                    }
                    walk(document, 'document');
                    const uniqueLines = Array.from(new Set(visibleLines));
                    const bodyText = uniqueLines.join('\\n');
                    const rejectionLines = uniqueLines.filter((line) =>
                        /n[aã]o foi poss[ií]vel processar|prompt|policy|pol[ií]tica|conte[uú]do|rejected|blocked|ocorreu um erro|tente novamente/i.test(line)
                    );
                    return {url: location.href, bodyText, rejectionLines, dialogs};
                }"""
            )
            visible_text_path.write_text(
                str(payload.get("bodyText") or ""), encoding="utf-8", errors="replace"
            )
            provider_reason_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            artifacts["visible_text_path"] = str(visible_text_path)
            artifacts["provider_reason_path"] = str(provider_reason_path)
        except (PatchrightTimeoutError, PatchrightError) as exc:
            visible_text_path.write_text(
                f"VISIBLE_TEXT_CAPTURE_FAILED: {type(exc).__name__}",
                encoding="utf-8",
            )
            provider_reason_path.write_text(
                json.dumps({"error": type(exc).__name__}, indent=2),
                encoding="utf-8",
            )
            artifacts["visible_text_path"] = str(visible_text_path)
            artifacts["provider_reason_path"] = str(provider_reason_path)

        return artifacts

    async def _safe_screenshot(self, job_id: int) -> Path | None:
        if self._page is None:
            return None
        self.config.screenshots_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.screenshots_dir / f"job_{job_id}_worker_failure.png"
        try:
            await self._page.screenshot(path=str(path), full_page=True)
            return path
        except (PatchrightTimeoutError, PatchrightError) as exc:
            event(
                self.logger,
                logging.ERROR,
                "failure_screenshot_failed",
                job_id=job_id,
                error=type(exc).__name__,
            )
            return None
