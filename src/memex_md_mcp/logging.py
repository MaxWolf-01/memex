"""File-based logging for memex."""

import logging
from importlib.metadata import version
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path.home() / ".local" / "share" / "memex-md-mcp"
LOG_FILE = LOG_DIR / "memex.log"
MAX_BYTES = 5 * 1024 * 1024


def get_logger() -> logging.Logger:
    """Get the memex logger, configuring it on first call."""
    logger = logging.getLogger("memex")

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=1,  # Keep 1 backup, max 10MB total
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s"))
    logger.addHandler(handler)

    logger.info("memex v%s started", version("memex-md-mcp"))

    return logger
