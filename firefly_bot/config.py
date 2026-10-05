"""ConfiguraÃ§Ã£o Ãºnica e imutÃ¡vel do projeto."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    """Valores deliberados para uma UI lenta, sem depender de arquivo externo."""

    # A UI pode demorar para materializar elementos mesmo apÃ³s a navegaÃ§Ã£o terminar.
    SELECTOR_TIMEOUT: int = 60_000
    # O Firefly Video em fair-use pode permanecer na fila por mais de dez minutos.
    # A aba precisa continuar aberta ou o prÃ³prio provedor descarta o progresso.
    GENERATION_BUDGET: int = 1_800_000
    # O watchdog recebe margem para exportaÃ§Ã£o e validaÃ§Ã£o apÃ³s a geraÃ§Ã£o.
    WATCHDOG_WALL_CLOCK: int = 2_100_000
    # NavegaÃ§Ã£o do Firefly Ã© pesada e nÃ£o deve falhar por um timeout de poucos segundos.
    NAV_TIMEOUT: int = 90_000
    # ExportaÃ§Ã£o pode iniciar renderizaÃ§Ã£o adicional antes de disponibilizar o arquivo.
    DOWNLOAD_TIMEOUT: int = 120_000
    UI_RESPONSE_TIMEOUT: int = 8_000
    DOWNLOAD_START_TIMEOUT: int = 12_000
    DOWNLOAD_COMPLETION_TIMEOUT: int = 300_000
    DOWNLOAD_STABILITY_CHECKS: int = 3
    DOWNLOAD_STABILITY_DELAY_MS: int = 1_000
    # Cada leitura do DOM deve ceder o controle; uma pÃ¡gina pesada nÃ£o pode
    # bloquear o slot inteiro atÃ© o watchdog de processo.
    STATE_READ_TIMEOUT: int = 15_000

    # Pausas variÃ¡veis entre jobs reduzem cadÃªncia rÃ­gida sem bloquear sincronizaÃ§Ã£o da UI.
    JITTER_MIN: int = 8
    JITTER_MAX: int = 20

    # Uma Ãºnica sessÃ£o Chrome pode atender vÃ¡rias abas. O padrÃ£o continua serial
    # para preservar compatibilidade; o operador habilita paralelismo pela CLI.
    DEFAULT_CONCURRENCY: int = 1
    MAX_CONCURRENT_TABS: int = 6
    # As abas permanecem simultÃ¢neas, mas os cliques de geraÃ§Ã£o nÃ£o acontecem
    # exatamente no mesmo instante.
    TAB_START_STAGGER_SECONDS: float = 3.0

    # Limite explÃ­cito impede tentativa infinita para o mesmo prompt com falha de infraestrutura.
    MAX_ATTEMPTS: int = 3
    # TrÃªs telas desconhecidas consecutivas indicam mudanÃ§a de UI ou bloqueio e exigem pausa.
    UNKNOWN_THRESHOLD: int = 3

    # Teto horÃ¡rio protege a conta e mantÃ©m a operaÃ§Ã£o dentro de um ritmo controlado.
    MAX_GENERATIONS_PER_HOUR: int = 20

    # Arquivos menores que 100 KB sÃ£o implausÃ­veis para um vÃ­deo exportado vÃ¡lido.
    MIN_FILE_SIZE_BYTES: int = 100_000

    # Caminhos relativos permitem mover o projeto sem alterar configuraÃ§Ã£o local.
    DB_PATH: str = "data/firefly_jobs.db"
    DOWNLOAD_DIR: str = "downloads"
    SCREENSHOT_DIR: str = "screenshots"
    OUTPUT_DIR: str = "saida"
    CHROME_PROFILE_DIR: str = "data/chrome_profile"

    # PÃ¡gina alvo e viewport estÃ¡vel para a execuÃ§Ã£o headed com Chrome real.
    FIREFLY_URL: str = "https://firefly.adobe.com/generate/video"
    VIEWPORT_WIDTH: int = 1920
    VIEWPORT_HEIGHT: int = 1080

    @classmethod
    def from_root(cls, root_dir: Path | None = None) -> Config:
        """Adaptador temporÃ¡rio para os entrypoints posteriores ao Sprint 1."""
        config = cls()
        object.__setattr__(config, "_root_dir", (root_dir or Path.cwd()).resolve())
        return config

    @property
    def root_dir(self) -> Path:
        return getattr(self, "_root_dir", Path.cwd().resolve())

    @property
    def db_path(self) -> Path:
        return self.root_dir / self.DB_PATH

    @property
    def profile_dir(self) -> Path:
        configured = os.environ.get("FIREFLY_CHROME_PROFILE_DIR") or os.environ.get("HSL_FIREFLY_CHROME_PROFILE")
        return Path(configured).resolve() if configured else self.root_dir / self.CHROME_PROFILE_DIR

    @property
    def downloads_dir(self) -> Path:
        return self.root_dir / self.DOWNLOAD_DIR

    @property
    def screenshots_dir(self) -> Path:
        return self.root_dir / self.SCREENSHOT_DIR

    @property
    def output_dir(self) -> Path:
        return self.root_dir / self.OUTPUT_DIR

    @property
    def selector_timeout_ms(self) -> int:
        return int(os.environ.get("FIREFLY_SELECTOR_TIMEOUT_MS", self.SELECTOR_TIMEOUT))

    @property
    def nav_timeout_ms(self) -> int:
        return self.NAV_TIMEOUT

    @property
    def export_timeout_ms(self) -> int:
        return self.DOWNLOAD_TIMEOUT

    @property
    def ui_response_timeout_ms(self) -> int:
        return self.UI_RESPONSE_TIMEOUT

    @property
    def download_start_timeout_ms(self) -> int:
        return self.DOWNLOAD_START_TIMEOUT

    @property
    def download_completion_timeout_ms(self) -> int:
        return self.DOWNLOAD_COMPLETION_TIMEOUT

    @property
    def download_stability_checks(self) -> int:
        return self.DOWNLOAD_STABILITY_CHECKS

    @property
    def download_stability_delay_seconds(self) -> float:
        return self.DOWNLOAD_STABILITY_DELAY_MS / 1000

    @property
    def state_read_timeout_seconds(self) -> float:
        return self.STATE_READ_TIMEOUT / 1000

    @property
    def generation_budget_seconds(self) -> int:
        override = os.environ.get("FIREFLY_GENERATION_BUDGET_MS") or os.environ.get(
            "GENERATION_BUDGET"
        )
        if override:
            return int(override) // 1000
        return self.GENERATION_BUDGET // 1000

    @property
    def watchdog_wall_clock_seconds(self) -> int:
        override = os.environ.get("FIREFLY_WATCHDOG_WALL_CLOCK_MS") or os.environ.get(
            "WATCHDOG_WALL_CLOCK"
        )
        if override:
            return int(override) // 1000
        return self.WATCHDOG_WALL_CLOCK // 1000

    def validate_concurrency(self, value: int) -> int:
        if not 1 <= value <= self.MAX_CONCURRENT_TABS:
            raise ValueError(
                f"concorrÃªncia precisa estar entre 1 e {self.MAX_CONCURRENT_TABS}: {value}"
            )
        return value

    @property
    def tab_start_stagger_seconds(self) -> float:
        return self.TAB_START_STAGGER_SECONDS

    @property
    def jitter_min_seconds(self) -> float:
        return float(self.JITTER_MIN)

    @property
    def jitter_max_seconds(self) -> float:
        return float(self.JITTER_MAX)

    @property
    def poll_interval_seconds(self) -> float:
        return 3.0

    @property
    def min_file_size_bytes(self) -> int:
        return self.MIN_FILE_SIZE_BYTES

    @property
    def chrome_channel(self) -> str:
        return "chrome"

    @property
    def firefly_url(self) -> str:
        return self.FIREFLY_URL

    @property
    def max_watchdog_restarts(self) -> int:
        return 5

    @property
    def watchdog_restart_window_seconds(self) -> int:
        return 3600

    @property
    def watchdog_backoff_cap_seconds(self) -> int:
        return 60

