"""Gerenciamento seguro do perfil Chrome exclusivo do worker no Windows."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path


class ChromeProfileBusyError(RuntimeError):
    """O perfil exclusivo continuou aberto depois da tentativa de encerramento."""


def _find_profile_roots(environment: dict[str, str]) -> list[int]:
    shell = shutil.which("pwsh.exe") or shutil.which("pwsh") or "powershell.exe"
    discovery = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-Command",
            (
                "$profile = $env:FIREFLY_BOT_PROFILE; "
                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                "Where-Object { $_.CommandLine -and "
                "$_.CommandLine.Contains($profile) -and "
                "$_.CommandLine -notmatch '--type=' } | "
                "ForEach-Object { $_.ProcessId }"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=60,
    )
    if discovery.returncode != 0:
        raise ChromeProfileBusyError(
            f"não foi possível verificar o perfil Chrome: {discovery.stderr.strip()}"
        )
    return [int(line) for line in discovery.stdout.splitlines() if line.strip().isdigit()]


def close_existing_profile_chrome(profile_dir: Path) -> list[int]:
    """Fecha somente processos Chrome raiz que usam exatamente o perfil do projeto."""
    if os.name != "nt":
        return []
    environment = os.environ.copy()
    environment["FIREFLY_BOT_PROFILE"] = str(profile_dir.resolve())
    process_ids = _find_profile_roots(environment)
    for process_id in process_ids:
        subprocess.run(
            ["taskkill.exe", "/PID", str(process_id), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=30,
        )
    if process_ids:
        time.sleep(0.5)
    remaining = _find_profile_roots(environment)
    if remaining:
        raise ChromeProfileBusyError(
            f"o Chrome do bot continua aberto (pids={remaining})"
        )
    return process_ids
