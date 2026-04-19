"""
Logger centralizado — Dark Channel Factory
Usa Rich para output bonito no terminal.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

# Console global compartilhado
console = Console(markup=True)

_configured = False


def setup_logging(log_level: str | None = None) -> None:
    """Configura logging global. Chame uma vez no entry point."""
    global _configured
    if _configured:
        return

    level_str = log_level or os.getenv("LOG_LEVEL", "INFO")
    level = getattr(logging, level_str.upper(), logging.INFO)

    # Handler de arquivo
    logs_dir = Path(os.getenv("LOGS_DIR", "output/logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "pipeline.log"

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                show_path=False,
                markup=True,
            ),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Retorna logger configurado para o módulo dado."""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
