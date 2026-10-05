"""Supervisor externo ao browser, responsável por timeout e reinício limitado."""

from __future__ import annotations

import logging
import os
import random
import signal
import subprocess
import sys
import time
from collections import deque

from .config import Config
from .logging_utils import event
from .worker import WORKER_NO_JOB, WORKER_PAUSED, WORKER_SUCCESS


class WatchdogSupervisor:
    """O pai não importa Patchright nem toca o browser do processo filho."""

    def __init__(
        self, config: Config, logger: logging.Logger, *, concurrency: int = 1
    ):
        self.config = config
        self.logger = logger
        self.concurrency = config.validate_concurrency(concurrency)
        self.restarts: deque[float] = deque()

    def run(self) -> int:
        while True:
            child = self._start_worker()
            try:
                exit_code = child.wait(timeout=self.config.watchdog_wall_clock_seconds)
            except subprocess.TimeoutExpired:
                event(
                    self.logger,
                    logging.ERROR,
                    "watchdog_timeout",
                    pid=child.pid,
                    timeout_seconds=self.config.watchdog_wall_clock_seconds,
                )
                self._kill_process_tree(child)
                exit_code = -1

            if exit_code == WORKER_SUCCESS:
                self._prune_restarts()
                delay = random.uniform(
                    self.config.jitter_min_seconds,
                    self.config.jitter_max_seconds,
                )
                event(
                    self.logger,
                    logging.INFO,
                    "between_jobs_delay",
                    delay_seconds=round(delay, 2),
                    configured_concurrency=self.concurrency,
                )
                time.sleep(delay)
                continue
            if exit_code in {WORKER_NO_JOB, WORKER_PAUSED}:
                return exit_code
            if not self._may_restart():
                event(self.logger, logging.CRITICAL, "watchdog_abort", restarts=len(self.restarts))
                return exit_code
            delay = min(2 ** len(self.restarts), self.config.watchdog_backoff_cap_seconds)
            event(self.logger, logging.WARNING, "watchdog_restart", delay_seconds=delay)
            time.sleep(delay)

    def _start_worker(self) -> subprocess.Popen[bytes]:
        command = [
            sys.executable,
            "-m",
            "firefly_bot.main",
            "--root",
            str(self.config.root_dir),
            "--concurrency",
            str(self.concurrency),
            "worker-once",
        ]
        if os.name == "posix":
            return subprocess.Popen(command, start_new_session=True)
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(command, creationflags=creationflags)

    def _kill_process_tree(self, child: subprocess.Popen[bytes]) -> None:
        if child.poll() is not None:
            return
        if os.name == "posix":
            os.killpg(os.getpgid(child.pid), signal.SIGKILL)
            child.wait()
            return
        # Windows não tem SIGKILL/process groups POSIX; /T encerra Chromium e todos os filhos.
        subprocess.run(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=20,
        )
        child.wait(timeout=20)

    def _prune_restarts(self) -> None:
        cutoff = time.monotonic() - self.config.watchdog_restart_window_seconds
        while self.restarts and self.restarts[0] < cutoff:
            self.restarts.popleft()

    def _may_restart(self) -> bool:
        self._prune_restarts()
        if len(self.restarts) >= self.config.max_watchdog_restarts:
            return False
        self.restarts.append(time.monotonic())
        return True
