"""Logging setup: console always, rotating file when LOG_FILE is set.

Every handler formats through :class:`RedactingFormatter`. Redacting at the
formatter rather than at each call site is deliberate: the leak that prompted
it came from ``httpx``, which logs the full request URL at INFO and therefore
put ``?apikey=...`` into the log through a library that has never heard of our
settings. No amount of care at our own call sites would have caught that, and
the same is true of any dependency added later. Formatting first and scrubbing
the result also covers exception tracebacks, where a request URL routinely
reappears several frames deep.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from app.config import Settings
from app.services.redaction import scrub, secrets_from

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


class RedactingFormatter(logging.Formatter):
    """Formats a record, then removes credentials from the finished string."""

    def __init__(self, fmt: str, secrets: tuple[str, ...]) -> None:
        super().__init__(fmt)
        self._secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record), self._secrets) or ""


def configure_logging(settings: Settings) -> None:
    """Attach handlers to the root logger, replacing any existing ones."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = RedactingFormatter(LOG_FORMAT, tuple(secrets_from(settings)))

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
