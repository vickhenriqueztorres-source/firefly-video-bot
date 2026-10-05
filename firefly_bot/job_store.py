"""Job Store SQLite com WAL, CAS, migração aditiva e alimentação por batch guide."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Config
from .generation_profile import BATCH_DEFAULTS
from .logging_utils import event

LOGGER = logging.getLogger("firefly_bot.job_store")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
@dataclass(frozen=True, slots=True)
class Job:
    id: int
    prompt: str
    image_path: str | None
    status: str
    attempts: int
    output_path: str | None
    error: str | None
    claimed_at: float | str | None
    generation_started_at: float | str | None
    updated_at: float | str
    model: str
    resolution: str
    aspect_ratio: str
    duration_seconds: int
    name: str | None
    download_started_at: float | str | None
    download_completed_at: float | str | None
    media_validated_at: float | str | None
    media_validation_status: str | None
    media_validation_error: str | None
    file_size_bytes: int | None
    sha256: str | None
    width: int | None
    height: int | None
    codec: str | None
    generate_audio: bool = False

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        values = dict(row)
        values["generate_audio"] = bool(values.get("generate_audio", 0))
        return cls(**values)


class ConcurrentTransitionError(RuntimeError):
    """Outro processo alterou o job antes da transição CAS."""


class GuideValidationError(ValueError):
    """O batch guide não é seguro ou não contém os campos obrigatórios."""


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")


def _open_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5, isolation_level=None)
    _configure_connection(conn)
    return conn


def _initialize_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt TEXT NOT NULL,
            image_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            output_path TEXT,
            error TEXT,
            claimed_at REAL,
            generation_started_at REAL,
            updated_at REAL NOT NULL,
            model TEXT NOT NULL DEFAULT 'Kling 2.5 Turbo',
            resolution TEXT NOT NULL DEFAULT '1080p',
            aspect_ratio TEXT NOT NULL DEFAULT '16:9',
            duration_seconds INTEGER NOT NULL DEFAULT 5,
            generate_audio INTEGER NOT NULL DEFAULT 0,
            name TEXT,
            CHECK (status IN ('pending', 'claimed', 'generating', 'stale_generating',
                             'done', 'failed-content', 'failed-infra', 'dead'))
        )
        """
    )
    additions = {
        "image_path": "TEXT",
        "generation_started_at": "REAL",
        "model": "TEXT NOT NULL DEFAULT 'Kling 2.5 Turbo'",
        "resolution": "TEXT NOT NULL DEFAULT '1080p'",
        "aspect_ratio": "TEXT NOT NULL DEFAULT '16:9'",
        "duration_seconds": "INTEGER NOT NULL DEFAULT 5",
        "generate_audio": "INTEGER NOT NULL DEFAULT 0",
        "name": "TEXT",
        "download_started_at": "REAL",
        "download_completed_at": "REAL",
        "media_validated_at": "REAL",
        "media_validation_status": "TEXT",
        "media_validation_error": "TEXT",
        "file_size_bytes": "INTEGER",
        "sha256": "TEXT",
        "width": "INTEGER",
        "height": "INTEGER",
        "codec": "TEXT",
    }
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(jobs)")}
    for column, declaration in additions.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {declaration}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            status TEXT NOT NULL,
            reason TEXT,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO system_state(singleton, status, reason, updated_at)
        VALUES (1, 'running', NULL, ?)
        """,
        (time.time(),),
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON jobs(status, id)")


def init_db(config: Config) -> sqlite3.Connection:
    """Abre e inicializa a base configurada sem apagar dados existentes."""
    conn = _open_connection(config.db_path)
    _initialize_schema(conn)
    event(LOGGER, logging.INFO, "db_initialized", db_path=config.db_path)
    return conn


def feed_prompts(conn: sqlite3.Connection, prompts: list[str]) -> int:
    """Insere prompts simples para manter compatibilidade com batches sem imagem."""
    rows = [
        (
            prompt.strip(),
            time.time(),
            BATCH_DEFAULTS["model"],
            BATCH_DEFAULTS["duration_seconds"],
        )
        for prompt in prompts
        if prompt.strip()
    ]
    if not rows:
        return 0
    with conn:
        conn.executemany(
            "INSERT INTO jobs(prompt, updated_at, model, duration_seconds) VALUES (?, ?, ?, ?)",
            rows,
        )
    event(LOGGER, logging.INFO, "prompts_fed", count=len(rows), status="pending")
    return len(rows)


def _safe_child(base: Path, relative: str, field: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise GuideValidationError(f"{field} deve ser um caminho relativo: {relative!r}")
    resolved_base = base.resolve()
    candidate = (resolved_base / relative).resolve()
    if not candidate.is_relative_to(resolved_base):
        raise GuideValidationError(f"{field} sai da pasta permitida: {relative!r}")
    return candidate


def _read_prompt(prompt_path: Path, item_name: str) -> str:
    if not prompt_path.is_file():
        raise FileNotFoundError(f"prompt ausente para name={item_name}: {prompt_path}")
    text = prompt_path.read_text(encoding="utf-8").strip()
    if not text:
        raise GuideValidationError(f"prompt vazio para name={item_name}: {prompt_path}")
    return text


def _validate_output_name(raw_name: object, fallback: str) -> str:
    name = str(raw_name or fallback).strip()
    forbidden = '<>:"/\\|?*'
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
    if (
        not name
        or name in {".", ".."}
        or name.endswith((" ", "."))
        or any(character in forbidden or ord(character) < 32 for character in name)
        or name.split(".", maxsplit=1)[0].upper() in reserved
    ):
        raise GuideValidationError(f"name não é um nome de arquivo Windows seguro: {name!r}")
    return name


def _discover_items(base_dir: Path) -> list[dict[str, str]]:
    images_dir = base_dir / "imagens"
    prompts_dir = images_dir / "prompts"
    if not images_dir.is_dir():
        raise FileNotFoundError(f"pasta de imagens não encontrada: {images_dir}")
    items: list[dict[str, str]] = []
    images = sorted(
        path
        for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    for image_path in images:
        prompt_path = prompts_dir / f"{image_path.stem}.txt"
        if not prompt_path.is_file():
            event(
                LOGGER,
                logging.WARNING,
                "guide_item_skipped",
                name=image_path.stem,
                reason="prompt_not_found",
                prompt_file=prompt_path,
            )
            continue
        items.append(
            {
                "image": image_path.name,
                "prompt_file": prompt_path.name,
                "name": image_path.stem,
            }
        )
    return items


def _batch_rows(
    guide: Mapping[str, object], base_dir: Path, items: Iterable[Mapping[str, object]]
) -> list[tuple[object, ...]]:
    images_dir = base_dir / "imagens"
    prompts_dir = images_dir / "prompts"
    rows: list[tuple[object, ...]] = []
    for raw_item in items:
        model = str(raw_item.get("model", guide.get("model", BATCH_DEFAULTS["model"])))
        resolution = str(
            raw_item.get("resolution", guide.get("resolution", BATCH_DEFAULTS["resolution"]))
        )
        aspect_ratio = str(
            raw_item.get(
                "aspect_ratio", guide.get("aspect_ratio", BATCH_DEFAULTS["aspect_ratio"])
            )
        )
        try:
            fps = int(raw_item.get("fps", guide.get("fps", BATCH_DEFAULTS["fps"])))
        except (TypeError, ValueError) as exc:
            raise GuideValidationError("fps precisa ser inteiro") from exc
        if fps != int(BATCH_DEFAULTS["fps"]):
            raise GuideValidationError(
                f"fps precisa ser {BATCH_DEFAULTS['fps']} para o perfil Kling 2.5 Turbo"
            )
        try:
            duration_seconds = int(
                raw_item.get(
                    "duration_seconds",
                    guide.get("duration_seconds", BATCH_DEFAULTS["duration_seconds"]),
                )
            )
        except (TypeError, ValueError) as exc:
            raise GuideValidationError("duration_seconds precisa ser inteiro") from exc
        if duration_seconds <= 0:
            raise GuideValidationError("duration_seconds precisa ser positivo")
        generate_audio = bool(
            raw_item.get(
                "generate_audio", guide.get("generate_audio", BATCH_DEFAULTS["generate_audio"])
            )
        )
        use_first_frame = bool(
            raw_item.get("use_first_frame", guide.get("use_first_frame", True))
        )
        image_name = str(raw_item.get("image", ""))
        image_path: Path | None = None
        if use_first_frame:
            image_path = _safe_child(images_dir, image_name, "image")
            if image_path.suffix.lower() not in IMAGE_EXTENSIONS or not image_path.is_file():
                raise FileNotFoundError(f"imagem inválida ou ausente: {image_path}")
        fallback_name = image_path.stem if image_path else "firefly_text_take"
        item_name = _validate_output_name(raw_item.get("name"), fallback_name)
        inline_prompt = raw_item.get("prompt")
        if inline_prompt is not None:
            prompt = str(inline_prompt).strip()
            if not prompt:
                raise GuideValidationError(f"prompt vazio para name={item_name}")
        else:
            if image_path is None:
                raise GuideValidationError(
                    f"prompt inline obrigatório para text-to-video name={item_name}"
                )
            prompt_file = str(raw_item.get("prompt_file", f"{image_path.stem}.txt"))
            prompt_path = _safe_child(prompts_dir, prompt_file, "prompt_file")
            prompt = _read_prompt(prompt_path, item_name)
        rows.append(
            (
                prompt,
                str(image_path) if image_path else None,
                time.time(),
                model,
                resolution,
                aspect_ratio,
                duration_seconds,
                int(generate_audio),
                item_name,
            )
        )
    return rows


def _insert_batch_rows(conn: sqlite3.Connection, rows: list[tuple[object, ...]]) -> int:
    if not rows:
        return 0
    with conn:
        conn.executemany(
            """
            INSERT INTO jobs (
                prompt, image_path, status, updated_at, model, resolution,
                aspect_ratio, duration_seconds, generate_audio, name
            ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    event(LOGGER, logging.INFO, "guide_fed", count=len(rows), status="pending")
    return len(rows)


def feed_from_guide(conn: sqlite3.Connection, guide_path: str | Path, base_dir: str | Path) -> int:
    """Carrega itens explícitos ou descobre pares imagem/prompt pelo nome-base."""
    guide_file = Path(guide_path).resolve()
    if not guide_file.is_file():
        raise FileNotFoundError(f"guia de produção não encontrado: {guide_file}")
    try:
        guide = json.loads(guide_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GuideValidationError(f"JSON inválido em {guide_file}: {exc}") from exc
    if not isinstance(guide, dict):
        raise GuideValidationError("a raiz do guia precisa ser um objeto JSON")
    root = Path(base_dir).resolve()
    raw_items = _discover_items(root) if guide.get("auto_discover") else guide.get("items", [])
    if not isinstance(raw_items, list):
        raise GuideValidationError("items precisa ser uma lista")
    rows = _batch_rows(guide, root, raw_items)
    return _insert_batch_rows(conn, rows)


def feed_auto_discover(conn: sqlite3.Connection, base_dir: str | Path) -> int:
    """Alimenta a fila sem JSON usando o perfil Kling 2.5 Turbo da raiz."""
    root = Path(base_dir).resolve()
    rows = _batch_rows(BATCH_DEFAULTS, root, _discover_items(root))
    return _insert_batch_rows(conn, rows)


def claim_job(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Faz claim atômico de um único job pendente."""
    now = time.time()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """
            UPDATE jobs
            SET status='claimed', attempts=attempts+1, claimed_at=?, updated_at=?, error=NULL
            WHERE id=(SELECT id FROM jobs WHERE status='pending' ORDER BY attempts ASC, id ASC LIMIT 1)
              AND status='pending'
            RETURNING id, prompt, image_path, status, attempts, output_path, error,
                      claimed_at, generation_started_at, updated_at, model, resolution,
                      aspect_ratio, duration_seconds, generate_audio, name, download_started_at,
                      download_completed_at, media_validated_at, media_validation_status,
                      media_validation_error, file_size_bytes, sha256, width, height, codec
            """,
            (now, now),
        ).fetchone()
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    if row is None:
        event(LOGGER, logging.INFO, "claim_empty", status="pending")
        return None
    claimed = dict(row)
    event(LOGGER, logging.INFO, "job_claimed", job_id=claimed["id"], status="claimed")
    return claimed


def _require_transition(
    conn: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...],
    job_id: int,
    status: str,
) -> None:
    cursor = conn.execute(sql, parameters)
    if cursor.rowcount != 1:
        event(LOGGER, logging.ERROR, "transition_conflict", job_id=job_id, status=status)
        raise ConcurrentTransitionError(f"job_id={job_id} target_status={status}")
    event(LOGGER, logging.INFO, "job_transition", job_id=job_id, status=status)


def mark_generating(conn: sqlite3.Connection, job_id: int) -> None:
    now = time.time()
    _require_transition(
        conn,
        """
        UPDATE jobs SET status='generating', generation_started_at=?, updated_at=?
        WHERE id=? AND status='claimed'
        """,
        (now, now, job_id),
        job_id,
        "generating",
    )


def mark_done(conn: sqlite3.Connection, job_id: int, output_path: str) -> None:
    _require_transition(
        conn,
        """
        UPDATE jobs SET status='done', output_path=?, updated_at=?
        WHERE id=? AND status='generating'
        """,
        (output_path, time.time(), job_id),
        job_id,
        "done",
    )


def mark_failed(
    conn: sqlite3.Connection, job_id: int, error: str, status: str = "failed-infra"
) -> None:
    if status not in {"failed-content", "failed-infra"}:
        raise ValueError(f"status de falha inválido: {status}")
    _require_transition(
        conn,
        "UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=? AND status='generating'",
        (status, error, time.time(), job_id),
        job_id,
        status,
    )


def mark_dead(conn: sqlite3.Connection, job_id: int, error: str) -> None:
    _require_transition(
        conn,
        "UPDATE jobs SET status='dead', error=?, updated_at=? WHERE id=? AND status='failed-infra'",
        (error, time.time(), job_id),
        job_id,
        "dead",
    )


def get_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status").fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def _reconcile(conn: sqlite3.Connection, budget: int, now: float) -> dict[str, int]:
    cutoff = now - budget
    conn.execute("BEGIN IMMEDIATE")
    try:
        claimed_reset = conn.execute(
            """
            UPDATE jobs SET status='pending', claimed_at=NULL,
                error='reconciliação: claim sem geração', updated_at=?
            WHERE status='claimed' AND generation_started_at IS NULL
            """,
            (now,),
        ).rowcount
        expired_reset = conn.execute(
            """
            UPDATE jobs SET status='pending', claimed_at=NULL,
                error='reconciliação: orçamento de geração expirado', updated_at=?
            WHERE status IN ('generating', 'stale_generating')
              AND generation_started_at IS NOT NULL
              AND CASE
                    WHEN typeof(generation_started_at) IN ('integer', 'real')
                    THEN generation_started_at
                    ELSE CAST(strftime('%s', generation_started_at) AS REAL)
                  END <= ?
            """,
            (now, cutoff),
        ).rowcount
        stale = conn.execute(
            """
            UPDATE jobs SET status='stale_generating',
                error='reconciliação: geração possivelmente ativa', updated_at=?
            WHERE status='generating' AND generation_started_at IS NOT NULL
              AND CASE
                    WHEN typeof(generation_started_at) IN ('integer', 'real')
                    THEN generation_started_at
                    ELSE CAST(strftime('%s', generation_started_at) AS REAL)
                  END > ?
            """,
            (now, cutoff),
        ).rowcount
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    result = {"claimed_reset": claimed_reset, "expired_reset": expired_reset, "stale": stale}
    event(LOGGER, logging.INFO, "jobs_reconciled", **result)
    return result


def reconcile_jobs(conn: sqlite3.Connection, generation_budget_seconds: int) -> dict[str, int]:
    return _reconcile(conn, generation_budget_seconds, time.time())


class JobStore:
    """Adaptador orientado a objeto usado pelo Worker e pelo Watchdog."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = _open_connection(self.db_path)
        _initialize_schema(conn)
        return conn

    def initialize(self) -> None:
        with self._connect():
            return

    def add_prompts(self, prompts: Iterable[str]) -> int:
        with self._connect() as conn:
            return feed_prompts(conn, list(prompts))

    def add_mateo_execution_job(self, *, prompt: str, image_path: Path, name: str) -> int:
        """Enfileira um contrato Mateo já decidido, sem alterar prompt ou parâmetros."""
        with self._connect() as conn:
            return _insert_batch_rows(conn, [(
                prompt,
                str(image_path.resolve()),
                time.time(),
                "Firefly Video",
                "720p",
                "9:16",
                5,
                0,
                name,
            )])

    def feed_from_guide(self, guide_path: Path, base_dir: Path) -> int:
        with self._connect() as conn:
            return feed_from_guide(conn, guide_path, base_dir)

    def feed_auto_discover(self, base_dir: Path) -> int:
        with self._connect() as conn:
            return feed_auto_discover(conn, base_dir)

    def claim_next(self) -> Job | None:
        with self._connect() as conn:
            row = claim_job(conn)
        return Job(**row) if row else None

    def transition(
        self,
        job_id: int,
        expected_status: str,
        new_status: str,
        *,
        error: str | None = None,
        output_path: str | None = None,
        generation_started: bool = False,
        media_metadata: Mapping[str, object] | None = None,
    ) -> None:
        now = time.time()
        started_at = now if generation_started else None
        media = media_metadata or {}
        with self._connect() as conn:
            _require_transition(
                conn,
                """
                UPDATE jobs SET status=?, error=?, output_path=COALESCE(?, output_path),
                    generation_started_at=COALESCE(?, generation_started_at),
                    download_started_at=COALESCE(?, download_started_at),
                    download_completed_at=COALESCE(?, download_completed_at),
                    media_validated_at=COALESCE(?, media_validated_at),
                    media_validation_status=COALESCE(?, media_validation_status),
                    media_validation_error=COALESCE(?, media_validation_error),
                    file_size_bytes=COALESCE(?, file_size_bytes),
                    sha256=COALESCE(?, sha256),
                    width=COALESCE(?, width),
                    height=COALESCE(?, height),
                    codec=COALESCE(?, codec),
                    updated_at=?
                WHERE id=? AND status=?
                """,
                (
                    new_status,
                    error,
                    output_path,
                    started_at,
                    media.get("download_started_at"),
                    media.get("download_completed_at"),
                    media.get("media_validated_at"),
                    media.get("media_validation_status"),
                    media.get("media_validation_error"),
                    media.get("file_size_bytes"),
                    media.get("sha256"),
                    media.get("width"),
                    media.get("height"),
                    media.get("codec"),
                    now,
                    job_id,
                    expected_status,
                ),
                job_id,
                new_status,
            )

    def return_to_pending(self, job_id: int, expected_status: str, reason: str) -> None:
        with self._connect() as conn:
            _require_transition(
                conn,
                """
                UPDATE jobs SET status='pending', error=?, claimed_at=NULL, updated_at=?
                WHERE id=? AND status=?
                """,
                (reason, time.time(), job_id, expected_status),
                job_id,
                "pending",
            )

    def requeue_unstarted_infra_failure(self, job_id: int) -> None:
        """Requeue only a proven pre-generation infrastructure failure.

        This deliberately refuses jobs with a generation timestamp or output,
        because retrying either case could duplicate a paid generation.
        """
        with self._connect() as conn:
            _require_transition(
                conn,
                """
                UPDATE jobs SET status='pending', claimed_at=NULL,
                    error='reconciliação manual: falha de infraestrutura antes da geração',
                    updated_at=?
                WHERE id=? AND status='failed-infra'
                  AND generation_started_at IS NULL
                  AND output_path IS NULL
                """,
                (time.time(), job_id),
                job_id,
                "pending",
            )

    def set_system_status(self, status: str, reason: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE system_state SET status=?, reason=?, updated_at=? WHERE singleton=1",
                (status, reason, time.time()),
            )

    def get_system_status(self) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM system_state WHERE singleton=1").fetchone()
        return str(row["status"])

    def reconcile(
        self, generation_budget_seconds: int, now: datetime | None = None
    ) -> dict[str, int]:
        timestamp = (now or datetime.now(UTC)).timestamp()
        with self._connect() as conn:
            return _reconcile(conn, generation_budget_seconds, timestamp)

    def list_jobs(self) -> list[Job]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, prompt, image_path, status, attempts, output_path, error,
                       claimed_at, generation_started_at, updated_at, model, resolution,
                       aspect_ratio, duration_seconds, generate_audio, name, download_started_at,
                       download_completed_at, media_validated_at, media_validation_status,
                       media_validation_error, file_size_bytes, sha256, width, height, codec
                FROM jobs ORDER BY id
                """
            ).fetchall()
        return [Job.from_row(row) for row in rows]

    def get_job(self, job_id: int) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, prompt, image_path, status, attempts, output_path, error,
                       claimed_at, generation_started_at, updated_at, model, resolution,
                       aspect_ratio, duration_seconds, generate_audio, name, download_started_at,
                       download_completed_at, media_validated_at, media_validation_status,
                       media_validation_error, file_size_bytes, sha256, width, height, codec
                FROM jobs WHERE id=?
                """,
                (job_id,),
            ).fetchone()
        return Job.from_row(row) if row else None
