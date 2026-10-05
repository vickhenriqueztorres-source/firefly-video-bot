"""Interface de linha de comando para alimentar e executar a fila."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from .config import Config
from .healthcheck import run_healthcheck
from .job_store import JobStore
from .logging_utils import configure_logging, event
from .mateo_execution_contract import enqueue_execution_package
from .duration_control_gate import run_gate as run_duration_control_gate
from .session import probe_session
from .watchdog import WatchdogSupervisor
from .worker import Worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="firefly-bot")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--concurrency",
        type=int,
        default=Config.DEFAULT_CONCURRENCY,
        metavar="N",
        help="quantidade de abas Firefly simultâneas (padrão: 1; máximo: 6)",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--feed-guide", type=Path, metavar="GUIA_JSON")
    actions.add_argument("--feed-auto", type=Path, metavar="DIRETORIO_BASE")
    actions.add_argument("--feed-mateo-package", type=Path, metavar="MOTION_PACKAGE_JSON")
    actions.add_argument("--duration-control-gate", type=int, metavar="SECONDS")
    actions.add_argument("--duration-slider-gate", type=int, metavar="SECONDS")
    actions.add_argument("--recover-result-ready-job", type=int, metavar="JOB_ID")
    actions.add_argument("--recover-running-job", type=int, metavar="JOB_ID")
    actions.add_argument("--requeue-unstarted-infra-job", type=int, metavar="JOB_ID")
    actions.add_argument("--probe-session", action="store_true", help="Verifica se a sessão do Firefly está autenticada")
    actions.add_argument("--run", action="store_true")
    actions.add_argument("--status", action="store_true")

    # Compatibilidade com a interface inicial e com o processo filho do watchdog.
    commands = parser.add_subparsers(dest="command")
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("file", type=Path, help="Um prompt por linha")
    commands.add_parser("status")
    commands.add_parser("healthcheck")
    run_command = commands.add_parser("run")
    run_command.add_argument(
        "--concurrency", type=int, default=argparse.SUPPRESS, metavar="N"
    )
    worker_once = commands.add_parser("worker-once")
    worker_once.add_argument(
        "--concurrency", type=int, default=argparse.SUPPRESS, metavar="N"
    )
    return parser


def _show_status(store: JobStore, logger: logging.Logger) -> int:
    event(logger, logging.INFO, "system_status", status=store.get_system_status())
    for job in store.list_jobs():
        event(
            logger,
            logging.INFO,
            "job",
            job_id=job.id,
            name=job.name,
            status=job.status,
            attempts=job.attempts,
            output_path=job.output_path,
        )
    return 0


def main() -> int:
    configure_logging()
    logger = logging.getLogger("firefly_bot")
    parser = build_parser()
    args = parser.parse_args()
    selected_actions = sum(
        bool(value)
        for value in (
            args.feed_guide,
            args.feed_auto,
            args.feed_mateo_package,
            args.duration_control_gate is not None,
            args.duration_slider_gate is not None,
            args.recover_result_ready_job is not None,
            args.recover_running_job is not None,
            args.requeue_unstarted_infra_job is not None,
            args.probe_session,
            args.run,
            args.status,
            args.command,
        )
    )
    if selected_actions != 1:
        parser.error("escolha exatamente uma ação")

    config = Config.from_root(args.root)
    try:
        concurrency = config.validate_concurrency(args.concurrency)
    except ValueError as exc:
        parser.error(str(exc))
    store = JobStore(config.db_path)
    store.initialize()

    if args.feed_guide is not None:
        guide_path = args.feed_guide.resolve()
        count = store.feed_from_guide(guide_path, guide_path.parent)
        event(logger, logging.INFO, "guide_enqueued", count=count, guide=str(guide_path))
        return 0
    if args.feed_auto is not None:
        base_dir = args.feed_auto.resolve()
        count = store.feed_auto_discover(base_dir)
        event(logger, logging.INFO, "auto_discovery_enqueued", count=count, base=str(base_dir))
        return 0
    if args.feed_mateo_package is not None:
        package_path = args.feed_mateo_package.resolve()
        count = enqueue_execution_package(store, package_path)
        event(
            logger,
            logging.INFO,
            "mateo_motion_package_enqueued",
            count=count,
            package=str(package_path),
        )
        return 0
    if args.duration_control_gate is not None:
        result = asyncio.run(
            run_duration_control_gate(
                root=args.root.resolve(),
                run_dir=Path(r"C:\B2-AI-STUDIO\mission-control\runs\FIREFLY-DURATION-CONTROL-001"),
                requested_seconds=args.duration_control_gate,
                logger=logger,
            )
        )
        return 0 if result["status"] in {"PASS", "FIREFLY_DURATION_UNSUPPORTED"} else 1
    if args.duration_slider_gate is not None:
        result = asyncio.run(
            run_duration_control_gate(
                root=args.root.resolve(),
                run_dir=Path(r"C:\B2-AI-STUDIO\mission-control\runs\FIREFLY-DURATION-SLIDER-002"),
                requested_seconds=args.duration_slider_gate,
                logger=logger,
                run_id="FIREFLY-DURATION-SLIDER-002",
            )
        )
        return 0 if result["status"] == "PASS" else 1
    if args.recover_result_ready_job is not None:
        return asyncio.run(Worker(config, store, logger).recover_result_ready_job(args.recover_result_ready_job))
    if args.recover_running_job is not None:
        return asyncio.run(Worker(config, store, logger).recover_result_ready_job(args.recover_running_job))
    if args.requeue_unstarted_infra_job is not None:
        store.requeue_unstarted_infra_failure(args.requeue_unstarted_infra_job)
        event(
            logger,
            logging.INFO,
            "unstarted_infra_job_requeued",
            job_id=args.requeue_unstarted_infra_job,
        )
        return 0
    if args.probe_session:
        res = asyncio.run(probe_session(config))
        print(json.dumps(res, indent=2))
        return 0 if res.get("authenticated") else 1
    if args.command == "enqueue":
        count = store.add_prompts(args.file.read_text(encoding="utf-8").splitlines())
        event(logger, logging.INFO, "prompts_enqueued", count=count)
        return 0
    if args.status or args.command == "status":
        return _show_status(store, logger)
    if args.command == "worker-once":
        return asyncio.run(Worker(config, store, logger).run_batch(concurrency))
    if args.command == "healthcheck":
        results = asyncio.run(run_healthcheck(config, logger))
        return 1 if any(result.detected for result in results) else 0
    if args.run or args.command == "run":
        return WatchdogSupervisor(config, logger, concurrency=concurrency).run()
    parser.error("ação não reconhecida")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
