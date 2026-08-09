"""Logging setup: console always, rotating file when LOG_FILE is set."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from app.config import Settings

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(settings: Settings) -> None:
    """Attach handlers to the root logger, replacing any existing ones."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file:
        Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                settings.log_file, maxBytes=10_000_000, backupCount=5, encoding="utf-8"
            )
        )

    for handler in handlers:
        handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level)

    # APScheduler logs every job submission at INFO; that is pure noise here.
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
