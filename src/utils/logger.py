"""Logging configuration."""

import logging
import sys
from pathlib import Path
from rich.logging import RichHandler
from ..config import settings


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance."""
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(getattr(logging, settings.log_level.upper()))

        # Console handler with rich
        console_handler = RichHandler(
            rich_tracebacks=True,
            show_time=True,
            show_path=False,
        )
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console_handler)

        # File handler
        if settings.log_file:
            settings.log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(settings.log_file)
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
            logger.addHandler(file_handler)

    return logger
