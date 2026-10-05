"""Logging estruturado e pequeno, com campos pesquisáveis."""

from __future__ import annotations

import logging
from typing import Any


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-5s | %(message)s",
    )


def event(logger: logging.Logger, level: int, action: str, **fields: Any) -> None:
    """Registra pares key=value sem esconder contexto de diagnóstico."""
    serialized = " ".join(f"{key}={value!s}" for key, value in fields.items())
    logger.log(level, "action=%s %s", action, serialized)
